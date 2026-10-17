-- An LLM job no call is owed for (ADR 0010): the judge drops its event, or the relevance
-- check found it is not about AI.

ALTER TABLE extraction_job DROP CONSTRAINT extraction_job_state_check;
ALTER TABLE extraction_job ADD CONSTRAINT extraction_job_state_check
    CHECK (state IN ('pending', 'done', 'needs_look', 'skipped'));
