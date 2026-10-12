-- GitHub Actions (ADR 0006): page bodies live in the store, since a runner's disk is gone
-- after each job, and an intake becoming 'queued' dispatches the intake workflow instead of
-- waking a worker on a box.

-- gzip of the page as fetched. stored_at protects a body a running job has just written
-- from the pruning another job does (Store.prune_pages).
CREATE TABLE page_body (
    content_hash text PRIMARY KEY,
    body bytea NOT NULL,
    stored_at timestamptz NOT NULL DEFAULT now()
);

DROP TRIGGER intake_queued ON intake;
DROP TRIGGER intake_requeued ON intake;
DROP FUNCTION notify_intake_queued();

-- pg_net sends the request after commit, so the workflow never starts before the row exists.
-- Without both Vault secrets (a local or test database) nothing is sent; the workflow's
-- hourly schedule then works the queue.
CREATE FUNCTION dispatch_intake_workflow() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path FROM CURRENT AS $$
DECLARE
    target text := (SELECT decrypted_secret FROM vault.decrypted_secrets
                    WHERE name = 'intake_dispatch_url');
    token text := (SELECT decrypted_secret FROM vault.decrypted_secrets
                   WHERE name = 'intake_dispatch_token');
BEGIN
    IF target IS NULL OR token IS NULL THEN
        RETURN NULL;
    END IF;
    PERFORM net.http_post(
        url := target,
        body := '{"ref": "main"}'::jsonb,
        headers := jsonb_build_object(
            'Authorization', 'Bearer ' || token,
            'Accept', 'application/vnd.github+json',
            'Content-Type', 'application/json',
            'User-Agent', 'munich-event-radar'
        )
    );
    RETURN NULL;
END
$$;
REVOKE ALL ON FUNCTION dispatch_intake_workflow() FROM PUBLIC;

CREATE TRIGGER intake_queued AFTER INSERT ON intake FOR EACH ROW
    WHEN (NEW.state = 'queued') EXECUTE FUNCTION dispatch_intake_workflow();
CREATE TRIGGER intake_requeued AFTER UPDATE OF state ON intake FOR EACH ROW
    WHEN (NEW.state = 'queued' AND OLD.state IS DISTINCT FROM 'queued')
    EXECUTE FUNCTION dispatch_intake_workflow();
