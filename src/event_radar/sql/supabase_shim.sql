-- The parts of Supabase that the radar's policies name, created only where missing
-- (ADR 0005, decision 9). On Supabase every object exists already and nothing runs, so this
-- never touches the auth schema there. Locally and in CI it makes RLS testable.
DO $shim$
DECLARE
    role_name text;
BEGIN
    FOREACH role_name IN ARRAY ARRAY['anon', 'authenticated', 'service_role'] LOOP
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = role_name) THEN
            BEGIN
                EXECUTE format('CREATE ROLE %I NOLOGIN NOINHERIT', role_name);
            EXCEPTION WHEN duplicate_object THEN NULL;  -- another database created it first
            END;
        END IF;
    END LOOP;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'authenticator') THEN
        BEGIN
            CREATE ROLE authenticator LOGIN NOINHERIT;
            GRANT anon, authenticated, service_role TO authenticator;
        EXCEPTION WHEN duplicate_object THEN NULL;
        END;
    END IF;
    IF (SELECT NOT rolbypassrls FROM pg_roles WHERE rolname = 'service_role') THEN
        ALTER ROLE service_role BYPASSRLS;
    END IF;

    IF to_regnamespace('auth') IS NULL THEN
        CREATE SCHEMA auth;
        GRANT USAGE ON SCHEMA auth TO anon, authenticated, service_role;
    END IF;
    IF to_regclass('auth.users') IS NULL THEN
        CREATE TABLE auth.users (id uuid PRIMARY KEY, email text UNIQUE);
    END IF;
    IF to_regprocedure('auth.uid()') IS NULL THEN
        -- Supabase's definition: PostgREST puts the JWT claims in request.jwt.claims.
        CREATE FUNCTION auth.uid() RETURNS uuid LANGUAGE sql STABLE AS $$
            SELECT coalesce(
                nullif(current_setting('request.jwt.claim.sub', true), ''),
                nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub'
            )::uuid
        $$;
    END IF;
END
$shim$;
