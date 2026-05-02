-- Local dev only: Supabase role and schema stubs for supabase/postgres standalone mode.
-- The supabase/postgres image has an event trigger on CREATE EXTENSION that grants
-- privileges to supabase_admin and related roles. Without these roles the trigger
-- fires and aborts, causing the container to exit(3).
-- In production these roles and the auth schema are provided by the Supabase platform.

DO $$ BEGIN CREATE ROLE supabase_admin NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN CREATE ROLE authenticator NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN CREATE ROLE anon NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN CREATE ROLE authenticated NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN CREATE ROLE service_role NOLOGIN BYPASSRLS; EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN CREATE ROLE supabase_auth_admin NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN CREATE ROLE supabase_storage_admin NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- auth schema and stubs used in RLS policies
CREATE SCHEMA IF NOT EXISTS auth;

GRANT USAGE ON SCHEMA auth TO postgres, anon, authenticated, service_role;

CREATE OR REPLACE FUNCTION auth.uid() RETURNS uuid
  LANGUAGE sql STABLE AS $$
  SELECT nullif(current_setting('request.jwt.claim.sub', true), '')::uuid
$$;

CREATE OR REPLACE FUNCTION auth.role() RETURNS text
  LANGUAGE sql STABLE AS $$
  SELECT nullif(current_setting('request.jwt.claim.role', true), '')::text
$$;

-- supabase_realtime publication referenced in 001_initial.sql.
-- Must be an empty (selective) publication so that 001_initial.sql can ADD TABLE to it.
-- On real Supabase the platform creates it this way; FOR ALL TABLES would break ADD TABLE.
DO $$ BEGIN
  CREATE PUBLICATION supabase_realtime;
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
