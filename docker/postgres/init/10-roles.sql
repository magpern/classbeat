-- Database roles, grants and defaults of ADR 0002 section 4.
--
-- Run as the superuser, against the application database, by the postgres image's init
-- (/docker-entrypoint-initdb.d) or by hand / in CI:
--
--   psql -v ON_ERROR_STOP=1 -h HOST -U postgres -d classbeat -f 10-roles.sql
--
-- Passwords are read from secret files, never from arguments or the environment:
--   $DATABASE_OWNER_PASSWORD_FILE  (default /run/secrets/database_owner_password)
--   $DATABASE_PASSWORD_FILE        (default /run/secrets/database_password)
-- The superuser's own password (postgres_password) is the image's POSTGRES_PASSWORD_FILE.
--
-- Idempotent: running it again repairs drift in what it manages (attributes, passwords,
-- memberships, role settings, grants and default privileges of the two roles). ACL kinds it
-- does not manage (large objects, types, parameters) are not touched.
-- Roles for SECURITY DEFINER functions (M3a) and classbeat_maint (M5) are created by the
-- milestones that need them.

\set ON_ERROR_STOP on
\set owner_password `cat "${DATABASE_OWNER_PASSWORD_FILE:-/run/secrets/database_owner_password}"`
\set app_password `cat "${DATABASE_PASSWORD_FILE:-/run/secrets/database_password}"`

SELECT (:'owner_password' = '' OR :'app_password' = '') AS empty_password,
       NOT (SELECT rolsuper FROM pg_roles WHERE rolname = current_user) AS not_superuser \gset
\if :empty_password
  DO $$ BEGIN RAISE EXCEPTION 'a database password secret file is missing or empty'; END $$;
\endif
\if :not_superuser
  DO $$ BEGIN RAISE EXCEPTION 'this script must run as the superuser'; END $$;
\endif

-- Roles. The migration role owns everything; the app role owns nothing.
SELECT 'CREATE ROLE classbeat_owner LOGIN'
 WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'classbeat_owner') \gexec
SELECT 'CREATE ROLE classbeat_app LOGIN'
 WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'classbeat_app') \gexec

ALTER ROLE classbeat_owner WITH LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE
  NOREPLICATION INHERIT CONNECTION LIMIT -1 PASSWORD :'owner_password';
ALTER ROLE classbeat_app WITH LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE
  NOREPLICATION NOINHERIT CONNECTION LIMIT -1 PASSWORD :'app_password';

-- Neither role is a member of any role (a BYPASSRLS membership would defeat RLS through SET ROLE).
SELECT format('REVOKE %I FROM %I GRANTED BY %I', r.rolname, m.rolname, g.rolname)
  FROM pg_auth_members am
  JOIN pg_roles r ON r.oid = am.roleid
  JOIN pg_roles m ON m.oid = am.member
  JOIN pg_roles g ON g.oid = am.grantor
 WHERE m.rolname IN ('classbeat_owner', 'classbeat_app') \gexec

-- Role defaults, so every connection of the app role has them. They limit accidents (a stuck
-- lock, a runaway query); injected SQL could still lower them (threat model T26).
-- Everything else set for the role (also per database) is dropped first, so a re-run repairs it.
ALTER ROLE classbeat_app RESET ALL;
SELECT format('ALTER ROLE classbeat_app IN DATABASE %I RESET ALL', current_database()) \gexec
ALTER ROLE classbeat_app SET statement_timeout = '30s';
ALTER ROLE classbeat_app SET lock_timeout = '5s';
ALTER ROLE classbeat_app SET idle_in_transaction_session_timeout = '60s';

-- The other databases of the server: PUBLIC cannot connect to them (classbeat_app only gets
-- CONNECT on this one).
REVOKE CONNECT ON DATABASE postgres FROM PUBLIC;
REVOKE CONNECT ON DATABASE template1 FROM PUBLIC;

-- The database. PUBLIC loses CONNECT and TEMP; both roles get CONNECT explicitly.
SELECT format('ALTER DATABASE %I OWNER TO classbeat_owner', current_database()) \gexec
SELECT format('REVOKE ALL ON DATABASE %I FROM PUBLIC', current_database()) \gexec
SELECT format('REVOKE ALL ON DATABASE %I FROM classbeat_app', current_database()) \gexec
SELECT format('GRANT CONNECT ON DATABASE %I TO classbeat_owner, classbeat_app',
              current_database()) \gexec

-- Schema public. PUBLIC loses USAGE and CREATE; the app role gets USAGE only.
ALTER SCHEMA public OWNER TO classbeat_owner;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
REVOKE ALL ON SCHEMA public FROM classbeat_app;
GRANT USAGE, CREATE ON SCHEMA public TO classbeat_owner;
GRANT USAGE ON SCHEMA public TO classbeat_app;

-- What the owner creates from now on. No TRUNCATE, REFERENCES, TRIGGER or MAINTAIN, and new
-- functions are not callable by everyone (a SECURITY DEFINER function is granted by name).
ALTER DEFAULT PRIVILEGES FOR ROLE classbeat_owner IN SCHEMA public
  REVOKE ALL ON TABLES FROM classbeat_app;
ALTER DEFAULT PRIVILEGES FOR ROLE classbeat_owner IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO classbeat_app;
ALTER DEFAULT PRIVILEGES FOR ROLE classbeat_owner IN SCHEMA public
  REVOKE ALL ON SEQUENCES FROM classbeat_app;
ALTER DEFAULT PRIVILEGES FOR ROLE classbeat_owner IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO classbeat_app;
ALTER DEFAULT PRIVILEGES FOR ROLE classbeat_owner REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
-- Global defaults (no IN SCHEMA) granted to the app role or PUBLIC are dropped too. PUBLIC keeps
-- its built-in USAGE on types, which the app needs for the owner's enum types.
ALTER DEFAULT PRIVILEGES FOR ROLE classbeat_owner REVOKE ALL ON TABLES FROM classbeat_app, PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE classbeat_owner REVOKE ALL ON SEQUENCES FROM classbeat_app, PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE classbeat_owner REVOKE ALL ON FUNCTIONS FROM classbeat_app;
ALTER DEFAULT PRIVILEGES FOR ROLE classbeat_owner REVOKE ALL ON TYPES FROM classbeat_app;
ALTER DEFAULT PRIVILEGES FOR ROLE classbeat_owner REVOKE ALL ON SCHEMAS FROM classbeat_app, PUBLIC;

-- The same for objects that already exist (this script may run again later).
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC, classbeat_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO classbeat_app;
-- Migration bookkeeping stays closed to the app (the baseline migration revokes it too).
SELECT 'REVOKE ALL ON TABLE public.alembic_version FROM classbeat_app, PUBLIC'
 WHERE to_regclass('public.alembic_version') IS NOT NULL \gexec
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM PUBLIC, classbeat_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO classbeat_app;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC;
