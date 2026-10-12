-- Intake as a queue (ADR 0004 step 3, ADR 0005): the worker on the box LISTENs on
-- "<store schema>.intake" and hears the id of every intake that becomes 'queued', however it
-- got there (an admin's suggestion, an approval, the old web app). NOTIFY is delivered on
-- commit, so the worker never sees a row before it exists.

CREATE FUNCTION notify_intake_queued() RETURNS trigger
LANGUAGE plpgsql SET search_path FROM CURRENT AS $$
BEGIN
    PERFORM pg_notify(TG_TABLE_SCHEMA || '.intake', NEW.id::text);
    RETURN NULL;
END
$$;
REVOKE ALL ON FUNCTION notify_intake_queued() FROM PUBLIC;

CREATE TRIGGER intake_queued AFTER INSERT ON intake FOR EACH ROW
    WHEN (NEW.state = 'queued') EXECUTE FUNCTION notify_intake_queued();
CREATE TRIGGER intake_requeued AFTER UPDATE OF state ON intake FOR EACH ROW
    WHEN (NEW.state = 'queued' AND OLD.state IS DISTINCT FROM 'queued')
    EXECUTE FUNCTION notify_intake_queued();
