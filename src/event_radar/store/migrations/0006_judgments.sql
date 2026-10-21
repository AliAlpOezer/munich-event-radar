-- What the LLM judged each event to be about (ADR 0018). A candidate is what the judgment
-- reads, keyed by its hash, so an unchanged page or event is asked about once; it waits with
-- no result until asked. An observation names the candidate of its event, and an event the
-- judgment it was last judged by, which holds until its new candidate is answered.

CREATE TABLE judgment (
    key text PRIMARY KEY,
    candidate jsonb NOT NULL,
    version text,
    model text,
    result jsonb,
    created_at timestamptz NOT NULL,
    answered_at timestamptz
);

ALTER TABLE observation ADD COLUMN judgment_key text;
ALTER TABLE event ADD COLUMN judgment_key text;
