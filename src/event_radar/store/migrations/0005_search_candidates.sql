-- A daily web search proposes pages for review (ADR 0016): an event candidate waits in
-- 'review' without its day and place, which the admin gives when approving it.

ALTER TABLE intake DROP CONSTRAINT intake_event_names_day_and_place;
ALTER TABLE intake ADD CONSTRAINT intake_event_names_day_and_place
    CHECK (kind = 'organisation' OR state IN ('review', 'rejected')
        OR (starts_on IS NOT NULL AND place IS NOT NULL));
