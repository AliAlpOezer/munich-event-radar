-- The Data API that PostgREST exposes (ADR 0005). Not migrated: the store drops this schema
-- and runs this file again whenever the file or the migrations change. Runs with
-- search_path = <api schema>, <store schema>, pg_temp, so objects land in the api schema and
-- functions pin that path.
-- Reads are security-invoker views (RLS decides); writes are definer functions that check
-- the caller themselves. Keep the events view equal to FastAPI's /api/events until step 4.

CREATE VIEW events WITH (security_invoker = true) AS
SELECT
    e.key,
    e.tier,
    e.drop_reason,
    COALESCE(e.tier_reasons, '[]') AS reasons,
    e.merge_basis,
    COALESCE(u.status, 'none') AS user_status,
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
    id, url, events_url, name, state, approved, profile, log, result, organiser_id, error,
    created_at, updated_at, submitted_by = (SELECT auth.uid()) AS mine
FROM intake;

CREATE FUNCTION me() RETURNS jsonb
LANGUAGE sql STABLE SET search_path FROM CURRENT AS $$
    SELECT jsonb_build_object('user_id', auth.uid(), 'admin', is_admin())
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

CREATE FUNCTION decide_intake(intake_id bigint, approve boolean) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path FROM CURRENT AS $$
#variable_conflict use_column
DECLARE
    current_state text;
    next_state text := CASE WHEN decide_intake.approve THEN 'queued' ELSE 'rejected' END;
BEGIN
    IF NOT is_admin() THEN
        RAISE EXCEPTION 'Only an admin can decide on a suggestion'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    SELECT state INTO current_state FROM intake WHERE id = decide_intake.intake_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'No intake with this id' USING ERRCODE = 'no_data_found';
    END IF;
    IF current_state NOT IN ('review', 'rejected') THEN
        RAISE EXCEPTION 'An intake in state % cannot be decided', current_state
            USING ERRCODE = 'object_not_in_prerequisite_state';
    END IF;
    UPDATE intake SET
        state = next_state,
        approved = decide_intake.approve,
        updated_at = now(),
        log = log || intake_log_entry('review',
            CASE WHEN decide_intake.approve THEN 'Approved by an admin.'
                ELSE 'Rejected by an admin.' END,
            CASE WHEN decide_intake.approve THEN 'ok' ELSE 'warn' END)
    WHERE id = decide_intake.intake_id;
    RETURN next_state;
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
GRANT EXECUTE ON FUNCTION me() TO anon, authenticated;
GRANT EXECUTE ON FUNCTION set_status(text, text), suggest_organisation(text, text, text),
    decide_intake(bigint, boolean) TO authenticated;
