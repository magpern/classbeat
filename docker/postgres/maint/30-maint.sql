-- The admin role classbeat_maint and its column allowlist (ADR 0011 section 5, issue AR1 #210).
--
-- Run as the superuser, after the migrations and 20-queue.sql, by the one-shot compose service
-- db-functions (docker/postgres/queue/db-functions.sh), in one transaction together with
-- 31-maint-password.psql, which sets the password from the database_maint_password secret file.
-- This file is plain SQL (no psql meta-commands), so the tests run it the same way.
--
-- Idempotent: running it again repairs the role's attributes, memberships and settings, revokes
-- every privilege classbeat_maint holds on anything in the database (table, column, sequence,
-- function, schema, database) and grants exactly the list below. Guard 11
-- (backend/tests/test_maint_privileges.py) checks the result as an allowlist, with a registry of
-- secret columns maint may never read; a change here needs the same change there.
--
-- What maint may do: SELECT only on the columns the admin area shows, never a secret-bearing one
-- (hashes, ciphertexts, nonces, tokens, OAuth and Spotify flow state, job input, class content,
-- messages, external ids and results); the writes of ADR 0011 section 5. Every transaction
-- starts read only (the role default) and classbeat.admin.db audits each one.

DO $$
BEGIN
  IF NOT (SELECT rolsuper FROM pg_catalog.pg_roles WHERE rolname = current_user) THEN
    RAISE EXCEPTION 'this script must run as the superuser';
  END IF;
  IF to_regclass('public.admin_audit') IS NULL THEN
    RAISE EXCEPTION 'migrate to head first: public.admin_audit does not exist';
  END IF;
  IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'classbeat_maint') THEN
    CREATE ROLE classbeat_maint LOGIN;
  END IF;
END $$;

ALTER ROLE classbeat_maint WITH LOGIN NOSUPERUSER BYPASSRLS NOCREATEDB NOCREATEROLE
  NOREPLICATION NOINHERIT CONNECTION LIMIT -1;

-- A member of no role, and no role is a member of it (either would widen what it can do).
DO $$
DECLARE
  r record;
BEGIN
  FOR r IN SELECT g.rolname AS granted, m.rolname AS member, x.rolname AS grantor
             FROM pg_catalog.pg_auth_members am
             JOIN pg_catalog.pg_roles g ON g.oid = am.roleid
             JOIN pg_catalog.pg_roles m ON m.oid = am.member
             JOIN pg_catalog.pg_roles x ON x.oid = am.grantor
            WHERE 'classbeat_maint' IN (g.rolname, m.rolname) LOOP
    EXECUTE format('REVOKE %I FROM %I GRANTED BY %I', r.granted, r.member, r.grantor);
  END LOOP;
END $$;

-- Role defaults: the app role's three timeouts, and every transaction read only unless
-- classbeat.admin.db switches it (SET TRANSACTION READ WRITE, then the audit row).
ALTER ROLE classbeat_maint RESET ALL;
DO $$
BEGIN
  EXECUTE format('ALTER ROLE classbeat_maint IN DATABASE %I RESET ALL', current_database());
END $$;
ALTER ROLE classbeat_maint SET statement_timeout = '30s';
ALTER ROLE classbeat_maint SET lock_timeout = '5s';
ALTER ROLE classbeat_maint SET idle_in_transaction_session_timeout = '60s';
ALTER ROLE classbeat_maint SET default_transaction_read_only = on;

-- Everything maint holds in this database goes first: table, column, sequence and function
-- privileges in every schema, then the schema and database privileges.
DO $$
DECLARE
  r record;
BEGIN
  FOR r IN SELECT n.nspname, c.relname, c.relkind
             FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace,
                  aclexplode(c.relacl) a
            WHERE a.grantee = 'classbeat_maint'::regrole GROUP BY 1, 2, 3 LOOP
    EXECUTE format('REVOKE ALL ON %s %I.%I FROM classbeat_maint',
                   CASE WHEN r.relkind = 'S' THEN 'SEQUENCE' ELSE 'TABLE' END, r.nspname, r.relname);
  END LOOP;
  FOR r IN SELECT n.nspname, c.relname, a.attname
             FROM pg_catalog.pg_attribute a JOIN pg_catalog.pg_class c ON c.oid = a.attrelid
             JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace, aclexplode(a.attacl) x
            WHERE x.grantee = 'classbeat_maint'::regrole GROUP BY 1, 2, 3 LOOP
    EXECUTE format('REVOKE ALL (%I) ON TABLE %I.%I FROM classbeat_maint',
                   r.attname, r.nspname, r.relname);
  END LOOP;
  FOR r IN SELECT p.oid::regprocedure::text AS signature
             FROM pg_catalog.pg_proc p, aclexplode(p.proacl) a
            WHERE a.grantee = 'classbeat_maint'::regrole LOOP
    EXECUTE format('REVOKE ALL ON ROUTINE %s FROM classbeat_maint', r.signature);
  END LOOP;
  FOR r IN SELECT n.nspname FROM pg_catalog.pg_namespace n, aclexplode(n.nspacl) a
            WHERE a.grantee = 'classbeat_maint'::regrole LOOP
    EXECUTE format('REVOKE ALL ON SCHEMA %I FROM classbeat_maint', r.nspname);
  END LOOP;
  EXECUTE format('REVOKE ALL ON DATABASE %I FROM classbeat_maint', current_database());
  EXECUTE format('GRANT CONNECT ON DATABASE %I TO classbeat_maint', current_database());
END $$;

GRANT USAGE ON SCHEMA public TO classbeat_maint;

-- Accounts (ADR 0011 section 6, Users): never password_hash; disable, enable and delete.
GRANT SELECT (id, email, email_normalized, email_verified_at, role, disabled_at, created_at)
  ON public.users TO classbeat_maint;
GRANT UPDATE (disabled_at), DELETE ON public.users TO classbeat_maint;
-- Sessions: only what revoking all of a user's sessions needs (never the token hash, IP or agent).
GRANT SELECT (id, user_id, created_at, last_seen_at, expires_at) ON public.sessions
  TO classbeat_maint;
GRANT DELETE ON public.sessions TO classbeat_maint;
-- Invites: never the code hash; disabling one expires it (the M1 consume path is unchanged).
GRANT SELECT (id, created_by, used_by, created_at, expires_at, used_at) ON public.invites
  TO classbeat_maint;
GRANT INSERT, UPDATE (expires_at) ON public.invites TO classbeat_maint;

-- Tiers (ADR 0011 sections 1 and 2).
GRANT SELECT (id, name, rank, classes_per_month, revisions_per_class, concurrent_jobs,
              monthly_cost_micros, route_key, features, created_at, updated_at, updated_by)
  ON public.plans TO classbeat_maint;
GRANT UPDATE (classes_per_month, revisions_per_class, concurrent_jobs, monthly_cost_micros,
              updated_at, updated_by)
  ON public.plans TO classbeat_maint;
GRANT SELECT (id, label, plan_id, features, grant_days, max_redemptions, redeemed_count,
              expires_at, disabled_at, created_by, created_at)
  ON public.redeem_codes TO classbeat_maint;
GRANT INSERT, UPDATE (disabled_at) ON public.redeem_codes TO classbeat_maint;
GRANT SELECT (id, owner_id, plan_id, features, source, starts_at, ends_at, code_id, granted_by,
              note, revoked_at, revoked_by, created_at)
  ON public.entitlements TO classbeat_maint;
GRANT INSERT, UPDATE (revoked_at, revoked_by) ON public.entitlements TO classbeat_maint;
GRANT SELECT (id, owner_id, code_id, entitlement_id, created_at)
  ON public.code_redemptions TO classbeat_maint;
-- The ledger trigger allows maint only committed -> refunded (migrations 0007 and 0008).
GRANT SELECT (id, owner_id, job_id, period, plan_id, state, reason, settled_at, refunded_by,
              created_at)
  ON public.credit_ledger TO classbeat_maint;
GRANT UPDATE (state, reason, settled_at, refunded_by) ON public.credit_ledger TO classbeat_maint;
GRANT SELECT (key, value, created_at, updated_at, updated_by) ON public.app_settings
  TO classbeat_maint;
GRANT INSERT, UPDATE ON public.app_settings TO classbeat_maint;
-- The audit log: insert and read (never the IP's HMAC); its trigger refuses UPDATE and DELETE.
GRANT SELECT (id, created_at, actor_id, actor_email, action, target_user_id, target_kind,
              target_id, params, outcome, reason, request_id)
  ON public.admin_audit TO classbeat_maint;
GRANT INSERT ON public.admin_audit TO classbeat_maint;

-- Status only (ADR 0011 section 6): which connections exist and how they are, never their
-- public data (the Spotify Client ID), ciphertexts, nonces or wrapped keys.
GRANT SELECT (id, owner_id, provider, status, last_error, connected_at, updated_at, last_used_at)
  ON public.connections TO classbeat_maint;
-- Usage and cost (ADR 0007 section 10): agent_runs holds no content.
GRANT SELECT (id, owner_id, job_id, stage, attempt, provider, model, model_answered, effort,
              prompt_version, turns, tool_calls, input_tokens, output_tokens, cache_read_tokens,
              cache_write_tokens, cost_micros, outcome, started_at, finished_at, job_kind,
              price_version)
  ON public.agent_runs TO classbeat_maint;
-- Jobs: counts per state, waiting approvals, failures by category; never input, keys or hashes.
GRANT SELECT (id, owner_id, kind, state, stage, error_category, credit_state, expires_at,
              approved_at, created_at, updated_at, started_at, finished_at)
  ON public.jobs TO classbeat_maint;
-- Uncertain external operations: kind, state and times; never keys, ids, results or context.
GRANT SELECT (id, owner_id, job_id, kind, state, error_category, created_at, updated_at,
              resolved_at, pending_at, call_ended_at)
  ON public.external_operations TO classbeat_maint;
