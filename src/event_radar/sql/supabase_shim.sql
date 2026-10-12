-- The parts of Supabase that the radar's policies and triggers name, created only where
-- missing (ADR 0005, decision 9). On Supabase every object exists already, so this never
-- touches the auth schema there; it only enables pg_net once. Locally and in CI it makes RLS
-- and the intake dispatch testable.
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

    -- pg_net and Vault (ADR 0006). Where Postgres offers pg_net, as Supabase does, it is
    -- enabled; elsewhere a stand-in records each request in a table of the same name.
    IF to_regnamespace('net') IS NULL THEN
        IF EXISTS (SELECT FROM pg_available_extensions WHERE name = 'pg_net') THEN
            CREATE EXTENSION pg_net;
        ELSE
            CREATE SCHEMA net;
            CREATE TABLE net.http_request_queue (
                id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                url text NOT NULL,
                headers jsonb NOT NULL,
                body jsonb
            );
            CREATE FUNCTION net.http_post(
                url text,
                body jsonb DEFAULT '{}',
                params jsonb DEFAULT '{}',
                headers jsonb DEFAULT '{}',
                timeout_milliseconds integer DEFAULT 5000
            ) RETURNS bigint LANGUAGE sql AS $$
                INSERT INTO net.http_request_queue (url, headers, body)
                VALUES (url, headers, body) RETURNING id
            $$;
        END IF;
    END IF;
    IF to_regnamespace('vault') IS NULL THEN
        CREATE SCHEMA vault;
        CREATE TABLE vault.decrypted_secrets (name text PRIMARY KEY, decrypted_secret text);
    END IF;
END
$shim$;
