-- Adding a single event from its page (ADR 0014): an intake of kind 'event' names the page,
-- the day and the place, which only test the page. An admin can take such an event off the
-- board for good: its intake becomes 'removed', and its page is not taken again.

ALTER TABLE intake
    ADD COLUMN kind text NOT NULL DEFAULT 'organisation'
        CHECK (kind IN ('organisation', 'event')),
    ADD COLUMN starts_on date,
    ADD COLUMN place text,
    ADD CONSTRAINT intake_event_names_day_and_place
        CHECK (kind = 'organisation' OR (starts_on IS NOT NULL AND place IS NOT NULL));
ALTER TABLE intake DROP CONSTRAINT intake_state_check;
ALTER TABLE intake ADD CONSTRAINT intake_state_check CHECK (state IN ('queued', 'checking',
    'profiling', 'review', 'finding', 'harvesting', 'done', 'rejected', 'failed', 'removed'));

-- `platforms.canonical` in SQL: scheme and host lower case, no trailing slash, no fragment.
-- tests/test_access.py holds the two to the same answers.
CREATE FUNCTION canonical_url(url text) RETURNS text
LANGUAGE sql IMMUTABLE SET search_path FROM CURRENT AS $$
    SELECT lower(m[1]) || '://' || lower(m[2]) || coalesce(nullif(rtrim(m[3], '/'), ''), '/')
        || coalesce('?' || nullif(m[4], ''), '')
    FROM regexp_match(btrim(url), '^([^:/?#]+)://([^/?#]*)([^?#]*)(?:\?([^#]*))?') AS m
$$;

-- One event intake per page while it is worked or once it is added (ADR 0014 D8); after a
-- refusal or a failure the page can be given again.
CREATE UNIQUE INDEX intake_one_per_event_page ON intake (canonical_url(url))
    WHERE kind = 'event' AND state NOT IN ('rejected', 'failed', 'removed');
CREATE INDEX intake_events_by_user ON intake (submitted_by, created_at) WHERE kind = 'event';

-- Take an event intake's page off the radar (ADR 0014 D7): its source, LLM jobs,
-- observations and fetches, and the events that page alone holds, with their aliases and
-- push ledger rows; users' statuses and stars go by cascade. An event that another source
-- also holds stays, and the next judge rewrites it without the page. A judge running
-- meanwhile would write back the events it read before, so this holds the judge's lock
-- (`Database.lock`), waiting for it or, without `wait`, refusing. Returns how many events
-- were deleted.
CREATE FUNCTION drop_intake_page(intake_id bigint, wait boolean) RETURNS integer
LANGUAGE plpgsql SET search_path FROM CURRENT AS $$
DECLARE
    page text := (SELECT url FROM source WHERE added_via = 'intake:' || intake_id);
    judge bigint := hashtext(current_schema() || '.judge');
    keys text[];
BEGIN
    IF page IS NULL THEN
        RETURN 0;
    END IF;
    IF wait THEN
        PERFORM pg_advisory_xact_lock(judge);
    ELSIF NOT pg_try_advisory_xact_lock(judge) THEN
        RAISE EXCEPTION 'The board is being updated; try again in a minute'
            USING ERRCODE = 'lock_not_available';
    END IF;
    SELECT coalesce(array_agg(key), '{}') INTO keys FROM event
    WHERE sources = jsonb_build_array(page);
    DELETE FROM event_alias WHERE event_key = ANY(keys);
    DELETE FROM push_ledger WHERE event_key = ANY(keys);
    DELETE FROM event WHERE key = ANY(keys);
    DELETE FROM extraction_job WHERE url = page;
    DELETE FROM observation WHERE url = page;
    DELETE FROM snapshot WHERE url = page;
    DELETE FROM source WHERE url = page;
    RETURN cardinality(keys);
END
$$;

REVOKE ALL ON FUNCTION canonical_url(text), drop_intake_page(bigint, boolean) FROM PUBLIC;
