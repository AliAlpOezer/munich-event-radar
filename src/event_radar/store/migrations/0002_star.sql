-- Stars: the events each user keeps (ADR 0007). A row is a star; unstarring deletes it.
-- Rows go with the user or the event; a merge moves them to the surviving event.

CREATE TABLE star (
    user_id uuid NOT NULL REFERENCES auth.users (id) ON DELETE CASCADE,
    event_key text NOT NULL REFERENCES event (key) ON DELETE CASCADE,
    starred_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, event_key)
);
CREATE INDEX star_by_event ON star (event_key);

ALTER TABLE star ENABLE ROW LEVEL SECURITY;
-- anon holds the grant only so the events view can join it; no policy gives anon a row.
GRANT SELECT ON star TO anon, authenticated;
CREATE POLICY own_rows ON star FOR SELECT TO authenticated
    USING (user_id = (SELECT auth.uid()));
