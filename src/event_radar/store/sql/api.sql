-- The Data API that PostgREST exposes (ADR 0005). Not migrated: the store drops this schema
-- and runs this file again whenever the file or the migrations change. Runs with
-- search_path = <api schema>, <store schema>, pg_temp, so objects land in the api schema and
-- functions pin that path.
-- Reads are security-invoker views (RLS decides); writes are definer functions that check
-- the caller themselves.

CREATE VIEW events WITH (security_invoker = true) AS
SELECT
    e.key,
    e.tier,
    e.drop_reason,
    COALESCE(e.tier_reasons, '[]') AS reasons,
    e.merge_basis,
    COALESCE(u.status, 'none') AS user_status,
    st.event_key IS NOT NULL AS starred,
    e.first_seen,
    e.updated_at,
    COALESCE(e.updated_at < last_ok_run_at(), false) AS stale,
    e.fields,
    -- An image on 3 or more events is a site's share image, not an event cover.
    CASE WHEN count(*) OVER (PARTITION BY e.fields #>> '{image,value}') < 3
        THEN e.fields #>> '{image,value}' END AS cover,
    s.sources,
    s.organiser_ids
FROM event e
LEFT JOIN user_state u ON u.event_key = e.key AND u.user_id = (SELECT auth.uid())
LEFT JOIN star st ON st.event_key = e.key AND st.user_id = (SELECT auth.uid())
CROSS JOIN LATERAL (
    SELECT
        COALESCE(jsonb_agg(jsonb_build_object(
            'url', x.url, 'class', src.source_class, 'organiser_id', src.organiser_id
        ) ORDER BY x.n), '[]') AS sources,
        COALESCE(jsonb_agg(DISTINCT src.organiser_id ORDER BY src.organiser_id)
            FILTER (WHERE src.organiser_id IS NOT NULL), '[]') AS organiser_ids
    FROM jsonb_array_elements_text(e.sources) WITH ORDINALITY AS x (url, n)
    LEFT JOIN source src ON src.url = x.url
) s;

CREATE VIEW organisers WITH (security_invoker = true) AS
SELECT
    o.id, o.name, o.domain, o.kind, o.ai_focus, o.status, o.homepage,
    o.discovered_via, o.discovered_at,
    COALESCE((
        SELECT jsonb_agg(jsonb_build_object(
            'url', s.url, 'kind', s.kind, 'source_class', s.source_class,
            'browser', s.browser, 'added_via', s.added_via,
            'last_fetched_at', s.last_fetched_at, 'last_ok_at', s.last_ok_at,
            'last_yield', s.last_yield, 'empty_streak', s.empty_streak,
            'error_streak', s.error_streak, 'last_error', s.last_error
        ) ORDER BY s.id)
        FROM source s WHERE s.organiser_id = o.id
    ), '[]') AS sources
FROM organiser o;

CREATE VIEW runs WITH (security_invoker = true) AS
SELECT id, started_at, finished_at, outcome, COALESCE(summary, '{}') AS summary FROM run;

CREATE VIEW intakes WITH (security_invoker = true) AS
SELECT
    id, kind, url, events_url, name, starts_on, place, state, approved, profile, log, result,
    organiser_id, error, created_at, updated_at, submitted_by = (SELECT auth.uid()) AS mine
FROM intake;

CREATE FUNCTION me() RETURNS jsonb
LANGUAGE sql STABLE SET search_path FROM CURRENT AS $$
    SELECT jsonb_build_object('user_id', auth.uid(), 'admin', is_admin())
$$;

-- What the page polls to know when to reload, and when the board was last harvested; the
-- run table itself is for admins only. The version moves when a run starts (a new id) or
-- ends (one more finished run), and when an intake adds events or organisers.
CREATE FUNCTION board_status() RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path FROM CURRENT AS $$
    SELECT jsonb_build_object(
        'harvested_at', (SELECT max(finished_at) FROM run WHERE outcome = 'ok'),
        'version', concat_ws('/',
            (SELECT concat_ws('/', max(id), count(finished_at)) FROM run),
            (SELECT concat_ws('/', count(*), max(updated_at)) FROM event),
            (SELECT count(*) FROM organiser))
    )
$$;

CREATE FUNCTION set_status(event_key text, status text) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path FROM CURRENT AS $$
#variable_conflict use_column
DECLARE
    uid uuid := auth.uid();
BEGIN
    IF uid IS NULL THEN
        RAISE EXCEPTION 'Log in to keep a status' USING ERRCODE = 'insufficient_privilege';
    END IF;
    INSERT INTO user_state (user_id, event_key, status, updated_at)
    VALUES (uid, set_status.event_key, set_status.status, now())
    ON CONFLICT (user_id, event_key)
        DO UPDATE SET status = excluded.status, updated_at = excluded.updated_at;
    RETURN set_status.status;
END
$$;

-- Idempotent, unlike a toggle: a repeated or retried call cannot undo the one before.
CREATE FUNCTION set_star(event_key text, starred boolean) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path FROM CURRENT AS $$
#variable_conflict use_column
DECLARE
    uid uuid := auth.uid();
BEGIN
    IF uid IS NULL THEN
        RAISE EXCEPTION 'Log in to star an event' USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF set_star.starred IS NULL THEN
        RAISE EXCEPTION 'Say whether to star the event' USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF set_star.starred THEN
        INSERT INTO star (user_id, event_key) VALUES (uid, set_star.event_key)
        ON CONFLICT (user_id, event_key) DO NOTHING;
    ELSE
        DELETE FROM star WHERE user_id = uid AND event_key = set_star.event_key;
    END IF;
    RETURN set_star.starred;
END
$$;

CREATE FUNCTION suggest_organisation(url text, events_url text DEFAULT NULL, name text DEFAULT NULL)
RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path FROM CURRENT AS $$
#variable_conflict use_column
DECLARE
    uid uuid := auth.uid();
    by_admin boolean := is_admin();
    home text := btrim(suggest_organisation.url);
    events_page text := nullif(btrim(suggest_organisation.events_url), '');
    label text := nullif(btrim(suggest_organisation.name), '');
    waiting integer;
    new_id bigint;
BEGIN
    IF uid IS NULL THEN
        RAISE EXCEPTION 'Log in to suggest an organisation' USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF home IS NULL OR home !~ '^https?://[^\s/]+[^\s]*$' OR length(home) > 2048
        OR (events_page IS NOT NULL
            AND (events_page !~ '^https?://[^\s/]+[^\s]*$' OR length(events_page) > 2048))
    THEN
        RAISE EXCEPTION 'Give a web address starting with http:// or https://'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF length(label) > 200 THEN
        RAISE EXCEPTION 'Keep the name under 200 characters'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT by_admin THEN
        PERFORM pg_advisory_xact_lock(hashtext(uid::text));
        SELECT count(*) INTO waiting FROM intake WHERE submitted_by = uid AND state = 'review';
        IF waiting >= 5 THEN
            RAISE EXCEPTION 'You have 5 suggestions waiting for review; wait for a decision first'
                USING ERRCODE = 'program_limit_exceeded';
        END IF;
    END IF;
    INSERT INTO intake (url, events_url, name, state, submitted_by, log, created_at, updated_at)
    VALUES (
        home, events_page, label, CASE WHEN by_admin THEN 'queued' ELSE 'review' END, uid,
        CASE WHEN by_admin THEN '[]'::jsonb ELSE intake_log_entry('review',
            'Suggested. Nothing is fetched until an admin approves it.', 'info') END,
        now(), now()
    )
    RETURNING id INTO new_id;
    RETURN new_id;
END
$$;

-- The day and the place an event intake is checked against (ADR 0014): a day not yet past
-- and a place in under 200 characters. Returns the place, trimmed.
CREATE FUNCTION event_place(starts_on date, place text) RETURNS text
LANGUAGE plpgsql STABLE SET search_path FROM CURRENT AS $$
DECLARE
    spot text := nullif(btrim(place), '');
BEGIN
    IF starts_on IS NULL OR starts_on < (now() AT TIME ZONE 'Europe/Berlin')::date THEN
        RAISE EXCEPTION 'Give the day of an event that has not happened yet'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF spot IS NULL OR length(spot) > 200 THEN
        RAISE EXCEPTION 'Give the place, such as the venue or the city, in under 200 characters'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    RETURN spot;
END
$$;

-- An admin approves or rejects an intake in review, or adds a rejected one anyway. An event
-- the web search found has no day and place yet (ADR 0016): approving it needs both.
CREATE FUNCTION decide_intake(intake_id bigint, approve boolean, starts_on date DEFAULT NULL,
    place text DEFAULT NULL) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path FROM CURRENT AS $$
#variable_conflict use_column
DECLARE
    current_state text;
    current_kind text;
    stored_day date;
    day date;
    spot text;
    next_state text := CASE WHEN decide_intake.approve THEN 'queued' ELSE 'rejected' END;
BEGIN
    IF NOT is_admin() THEN
        RAISE EXCEPTION 'Only an admin can decide on a suggestion'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    SELECT state, kind, starts_on INTO current_state, current_kind, stored_day
    FROM intake WHERE id = decide_intake.intake_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'No intake with this id' USING ERRCODE = 'no_data_found';
    END IF;
    IF current_state NOT IN ('review', 'rejected') THEN
        RAISE EXCEPTION 'An intake in state % cannot be decided', current_state
            USING ERRCODE = 'object_not_in_prerequisite_state';
    END IF;
    IF decide_intake.approve AND current_kind = 'event' AND (stored_day IS NULL
        OR decide_intake.starts_on IS NOT NULL OR decide_intake.place IS NOT NULL)
    THEN
        spot := event_place(decide_intake.starts_on, decide_intake.place);
        day := decide_intake.starts_on;
    END IF;
    UPDATE intake SET
        state = next_state,
        approved = decide_intake.approve,
        starts_on = COALESCE(day, starts_on),
        place = COALESCE(spot, place),
        updated_at = now(),
        log = log || intake_log_entry('review',
            CASE WHEN decide_intake.approve THEN 'Approved by an admin.'
                ELSE 'Rejected by an admin.' END,
            CASE WHEN decide_intake.approve THEN 'ok' ELSE 'warn' END)
    WHERE id = decide_intake.intake_id;
    RETURN next_state;
END
$$;

-- Adding one event from its page (ADR 0014). It is checked at once, with no review: the
-- page must name the day and the place. Caps per account that is not an admin (D8): 5 being
-- checked and 10 a day; and one intake per page, which a page an admin removed never passes
-- again unless an admin gives it.
CREATE FUNCTION suggest_event(url text, starts_on date, place text) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path FROM CURRENT AS $$
#variable_conflict use_column
DECLARE
    uid uuid := auth.uid();
    by_admin boolean := is_admin();
    page text := btrim(suggest_event.url);
    spot text;
    taken text;
    waiting_id bigint;
    open_count integer;
    day_count integer;
    new_id bigint;
BEGIN
    IF uid IS NULL THEN
        RAISE EXCEPTION 'Log in to add an event' USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF page IS NULL OR page !~ '^https?://[^\s/]+[^\s]*$' OR length(page) > 2048 THEN
        RAISE EXCEPTION 'Give the event''s web address, starting with http:// or https://'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    spot := event_place(suggest_event.starts_on, suggest_event.place);
    PERFORM pg_advisory_xact_lock(hashtext('event-intake ' || canonical_url(page)));
    SELECT id, state INTO waiting_id, taken FROM intake
    WHERE kind = 'event' AND canonical_url(intake.url) = canonical_url(page)
        AND state NOT IN ('rejected', 'failed') AND (state <> 'removed' OR NOT by_admin)
    ORDER BY state = 'removed' DESC
    LIMIT 1;
    IF taken = 'removed' THEN
        RAISE EXCEPTION 'An admin removed this event from the radar'
            USING ERRCODE = 'insufficient_privilege';
    ELSIF taken = 'done' THEN
        RAISE EXCEPTION 'This event was added already' USING ERRCODE = 'unique_violation';
    ELSIF taken IS NOT NULL AND taken <> 'review' THEN
        RAISE EXCEPTION 'This event is being checked already' USING ERRCODE = 'unique_violation';
    END IF;
    IF NOT by_admin THEN
        PERFORM pg_advisory_xact_lock(hashtext(uid::text));
        SELECT count(*) FILTER (WHERE state IN ('queued', 'checking', 'harvesting')),
            count(*) FILTER (WHERE created_at > now() - interval '1 day')
        INTO open_count, day_count
        FROM intake WHERE kind = 'event' AND submitted_by = uid;
        IF open_count >= 5 THEN
            RAISE EXCEPTION 'You have 5 events being checked; wait for them first'
                USING ERRCODE = 'program_limit_exceeded';
        END IF;
        IF day_count >= 10 THEN
            RAISE EXCEPTION 'You added 10 events in the last day; try again tomorrow'
                USING ERRCODE = 'program_limit_exceeded';
        END IF;
    END IF;
    -- A page the web search found and an admin has not decided on yet is the person's now.
    IF taken = 'review' THEN
        UPDATE intake SET
            starts_on = suggest_event.starts_on,
            place = spot,
            state = 'queued',
            submitted_by = uid,
            updated_at = now(),
            log = log || intake_log_entry('review', 'Added by a person, with its day and place.',
                'ok')
        WHERE id = waiting_id;
        RETURN waiting_id;
    END IF;
    INSERT INTO intake (kind, url, starts_on, place, state, submitted_by, created_at, updated_at)
    VALUES ('event', page, suggest_event.starts_on, spot, 'queued', uid, now(), now())
    RETURNING id INTO new_id;
    RETURN new_id;
END
$$;

-- An admin takes an added event off the board, and its page stays off (ADR 0014 D7): one
-- transaction, so an event is never half removed. Returns how many events were deleted.
CREATE FUNCTION remove_event(intake_id bigint) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path FROM CURRENT AS $$
#variable_conflict use_column
DECLARE
    found_kind text;
    current_state text;
    deleted integer;
BEGIN
    IF NOT is_admin() THEN
        RAISE EXCEPTION 'Only an admin can remove an event'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    SELECT kind, state INTO found_kind, current_state
    FROM intake WHERE id = remove_event.intake_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'No intake with this id' USING ERRCODE = 'no_data_found';
    END IF;
    IF found_kind <> 'event' THEN
        RAISE EXCEPTION 'Only an added event can be removed' USING ERRCODE = 'wrong_object_type';
    END IF;
    IF current_state = 'removed' THEN
        RETURN 0;
    END IF;
    IF current_state NOT IN ('done', 'rejected', 'failed') THEN
        RAISE EXCEPTION 'This event is still being checked; remove it once that is done'
            USING ERRCODE = 'object_not_in_prerequisite_state';
    END IF;
    deleted := drop_intake_page(remove_event.intake_id, false);
    UPDATE intake SET
        state = 'removed',
        updated_at = now(),
        log = log || intake_log_entry('removed', 'Removed from the board by an admin.', 'warn')
    WHERE id = remove_event.intake_id;
    RETURN deleted;
END
$$;

DO $$
BEGIN
    EXECUTE format('GRANT USAGE ON SCHEMA %I TO anon, authenticated, service_role',
        current_schema());
    EXECUTE format('REVOKE ALL ON ALL FUNCTIONS IN SCHEMA %I FROM PUBLIC', current_schema());
END
$$;
GRANT SELECT ON events, organisers TO anon, authenticated;
GRANT SELECT ON runs, intakes TO authenticated;
GRANT EXECUTE ON FUNCTION me(), board_status() TO anon, authenticated;
GRANT EXECUTE ON FUNCTION set_status(text, text), set_star(text, boolean),
    suggest_organisation(text, text, text), decide_intake(bigint, boolean, date, text),
    suggest_event(text, date, text), remove_event(bigint) TO authenticated;
