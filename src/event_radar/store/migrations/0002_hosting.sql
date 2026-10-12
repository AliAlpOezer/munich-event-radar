-- Hosting (ADR 0005): status per user, admins, who suggested an intake, and RLS. Runs with
-- search_path = <store schema>, pg_temp, after the Supabase shim. Grants name the API roles;
-- the api schema reads these tables through security-invoker views.

CREATE TABLE admin (
    user_id uuid PRIMARY KEY REFERENCES auth.users (id) ON DELETE CASCADE,
    role text NOT NULL CHECK (role IN ('owner', 'admin')),
    added_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX admin_one_owner ON admin (role) WHERE role = 'owner';

-- Alp's statuses predate logins; they wait here until `grant-admin --owner` claims them.
-- A 'none' row says nothing, and a row whose event is gone cannot be shown.
ALTER TABLE user_state RENAME TO user_state_unclaimed;
ALTER TABLE user_state_unclaimed RENAME CONSTRAINT user_state_pkey TO user_state_unclaimed_pkey;
DELETE FROM user_state_unclaimed u
    WHERE u.status = 'none' OR NOT EXISTS (SELECT FROM event e WHERE e.key = u.event_key);
ALTER TABLE user_state_unclaimed
    ADD FOREIGN KEY (event_key) REFERENCES event (key) ON DELETE CASCADE,
    ADD CHECK (status IN ('none', 'applied', 'registered', 'skipped'));

CREATE TABLE user_state (
    user_id uuid NOT NULL REFERENCES auth.users (id) ON DELETE CASCADE,
    event_key text NOT NULL REFERENCES event (key) ON DELETE CASCADE,
    status text NOT NULL CHECK (status IN ('none', 'applied', 'registered', 'skipped')),
    feedback integer,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, event_key)
);
CREATE INDEX user_state_by_event ON user_state (event_key);

-- The one authority on status changes. A new row may hold any status, since every status is
-- reachable from 'none'; that also keeps INSERT ... ON CONFLICT DO UPDATE usable.
CREATE FUNCTION check_status_transition() RETURNS trigger
LANGUAGE plpgsql SET search_path FROM CURRENT AS $$
BEGIN
    IF (OLD.status, NEW.status) NOT IN (
        ('none', 'applied'), ('none', 'registered'), ('none', 'skipped'),
        ('applied', 'registered'), ('applied', 'skipped'), ('applied', 'none'),
        ('registered', 'none'), ('skipped', 'none')
    ) THEN
        RAISE EXCEPTION '% cannot become %', OLD.status, NEW.status
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER status_transition BEFORE UPDATE OF status ON user_state FOR EACH ROW
    WHEN (OLD.status IS DISTINCT FROM NEW.status) EXECUTE FUNCTION check_status_transition();
CREATE TRIGGER status_transition BEFORE UPDATE OF status ON user_state_unclaimed FOR EACH ROW
    WHEN (OLD.status IS DISTINCT FROM NEW.status) EXECUTE FUNCTION check_status_transition();

ALTER TABLE intake ADD COLUMN submitted_by uuid REFERENCES auth.users (id) ON DELETE SET NULL;
CREATE INDEX intake_in_review_by_user ON intake (submitted_by) WHERE state = 'review';

-- A definer, so it answers false for anon, who cannot read the admin table.
CREATE FUNCTION is_admin() RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path FROM CURRENT AS $$
    SELECT EXISTS (SELECT FROM admin WHERE user_id = auth.uid())
$$;

-- Staleness is public, the run log is not: this returns one timestamp and nothing else.
CREATE FUNCTION last_ok_run_at() RETURNS timestamptz
LANGUAGE sql STABLE SECURITY DEFINER SET search_path FROM CURRENT AS $$
    SELECT max(started_at) FROM run WHERE outcome = 'ok'
$$;

-- One entry for intake.log, in the shape Store.log_intake writes.
CREATE FUNCTION intake_log_entry(step text, text text, level text) RETURNS jsonb
LANGUAGE sql STABLE SET search_path FROM CURRENT AS $$
    SELECT jsonb_build_array(jsonb_build_object(
        'at', to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"+00:00"'),
        'step', step, 'text', text, 'level', level
    ))
$$;

REVOKE ALL ON FUNCTION check_status_transition(), is_admin(), last_ok_run_at(),
    intake_log_entry(text, text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION is_admin(), last_ok_run_at() TO anon, authenticated;

DO $$
BEGIN
    EXECUTE format('GRANT USAGE ON SCHEMA %I TO anon, authenticated', current_schema());
END
$$;

-- Every table gets RLS (the store re-asserts it after each migration); only these get policies.
ALTER TABLE event ENABLE ROW LEVEL SECURITY;
ALTER TABLE organiser ENABLE ROW LEVEL SECURITY;
ALTER TABLE source ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_state ENABLE ROW LEVEL SECURITY;
ALTER TABLE admin ENABLE ROW LEVEL SECURITY;
ALTER TABLE intake ENABLE ROW LEVEL SECURITY;
ALTER TABLE run ENABLE ROW LEVEL SECURITY;

GRANT SELECT ON event, organiser, source TO anon, authenticated;
CREATE POLICY public_read ON event FOR SELECT TO anon, authenticated USING (true);
CREATE POLICY public_read ON organiser FOR SELECT TO anon, authenticated USING (true);
CREATE POLICY public_read ON source FOR SELECT TO anon, authenticated USING (true);

-- anon holds the grant only so the events view can join it; no policy gives anon a row.
GRANT SELECT ON user_state TO anon, authenticated;
CREATE POLICY own_rows ON user_state FOR SELECT TO authenticated
    USING (user_id = (SELECT auth.uid()));

GRANT SELECT ON admin, intake, run TO authenticated;
CREATE POLICY own_row ON admin FOR SELECT TO authenticated USING (user_id = (SELECT auth.uid()));
CREATE POLICY own_or_admin ON intake FOR SELECT TO authenticated
    USING (submitted_by = (SELECT auth.uid()) OR (SELECT is_admin()));
CREATE POLICY admin_only ON run FOR SELECT TO authenticated USING ((SELECT is_admin()));
