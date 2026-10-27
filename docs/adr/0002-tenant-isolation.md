# ADR 0002: Tenant isolation with UserContext and Postgres RLS

Status: Accepted

## Context

Every user's classes, connections, tokens, jobs and playlist links must stay theirs (roadmap
§1, P0). A single missed `WHERE owner_id = …` in a repository, a model-supplied ID, or a
background job running with the wrong user would leak data or write to someone else's MOWL
or Spotify account. The roadmap asks for two layers: application code that always carries the
authenticated user, and Postgres Row-Level Security underneath as defence in depth. This ADR
fixes the contract so #5 can implement it and every later table follows it.

## Decision

### 1. UserContext

```python
@dataclass(frozen=True, slots=True)
class UserContext:
    user_id: UUID
    role: Literal["user", "admin"]
    entitlements: frozenset[str] = frozenset()
```

- Lives in `classbeat.context`. Immutable, request- or job-scoped, never stored globally.
- Built **only** by two issuers: `classbeat.auth` from the authenticated server-side session
  (M1) and `classbeat.jobs` from the job row the worker claimed (M3a). Until those exist, only
  tests build one.
- A guard test scans the source (AST) and fails if `UserContext(` is called anywhere else.
  API handlers get it through one FastAPI dependency; they never take a user ID from a path,
  query, body, header or model output.
- Every service and repository function that touches owned data takes `ctx: UserContext`
  explicitly. IDs from the browser or a model are looked up *within* the owner's rows; not
  found and not owned are the same answer (404).

### 2. Owned tables

Every table holding per-user data has `owner_id uuid NOT NULL REFERENCES users(id)` and this
RLS, applied by the migration helper `classbeat.db.rls.enable_owner_rls(table)`:

```sql
ALTER TABLE <t> ENABLE ROW LEVEL SECURITY;
ALTER TABLE <t> FORCE ROW LEVEL SECURITY;
CREATE POLICY owner_isolation ON <t>
  FOR ALL
  USING      (owner_id = NULLIF(current_setting('app.user_id', true), '')::uuid)
  WITH CHECK (owner_id = NULLIF(current_setting('app.user_id', true), '')::uuid);
```

- `current_setting(…, true)` is NULL before the setting is first used in a session and `''`
  after a transaction-local value has ended, so `NULLIF` turns both into NULL: **no setting,
  no rows**, never an error and never everything.
- `FORCE` makes the policy apply to the table owner too.
- Repositories still filter `owner_id = ctx.user_id` themselves; RLS is the second layer, not
  the only one.

### 3. System tables

Tables that are not per-user, or that must be read before a user is known, are listed in
`classbeat.db.SYSTEM_TABLES` with a one-line reason each: for example the users and identities
tables used at sign-in (M1), the job-queue claim table (M3a), and shared music analysis (§6).
Cross-user operations (signing in, claiming a queued job, the lease reaper) touch only system
tables; after a claim, the worker builds the `UserContext` from the job row and reads the
job's owned data in a user session. Each milestone that adds a system table defines its access
rules in its own ADR.

### 4. Database roles

| Role | Login | Used by | Rights |
|---|---|---|---|
| `postgres` (superuser) | yes | the db container's init script only | creates the roles and the database |
| `classbeat_owner` | yes | `migrate` (Alembic) | owns the database, the `public` schema and every table; not superuser; never used by web or worker |
| `classbeat_app` | yes | web and worker | `NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE`; owns nothing; `SELECT, INSERT, UPDATE, DELETE` on tables and `USAGE, SELECT` on sequences through `ALTER DEFAULT PRIVILEGES FOR ROLE classbeat_owner`; **no** `TRUNCATE` (it ignores RLS), `REFERENCES` or `TRIGGER` |
| `classbeat_maint` | yes | audited admin and maintenance paths (created in M5) | `BYPASSRLS`; never used in a normal user's request path; every use writes an audit row |

- Compose creates the roles in `docker/postgres/init/` from secret files:
  `postgres_password` (superuser), `database_owner_password`, `database_password` (app).
- `PUBLIC` loses `CREATE` on the schema and `TEMP` on the database; `classbeat_app` gets
  `CONNECT` and schema `USAGE` only.
- Settings: web and worker use `CLASSBEAT_DATABASE_URL` + `database_password`;
  `migrate` uses `CLASSBEAT_MIGRATIONS_DATABASE_URL` + `database_owner_password`.

### 5. Sessions

```python
async with user_session(sessionmaker, ctx) as session:
    ...  # owned and system tables, as ctx.user_id
async with system_session(sessionmaker) as session:
    ...  # system tables only; owned tables return no rows
```

- `user_session` begins a transaction, runs
  `SELECT set_config('app.user_id', :user_id, true)` (transaction-local), yields the session,
  commits on success and rolls back on an exception. The setting ends with the transaction, so
  a pooled connection never carries a user into the next checkout.
- Never a session-level `SET app.user_id`. The pool resets connections on return (rollback).
- One transaction per `user_session`; a function needing two users' data is a design error.

### 6. Guard tests (from #5, kept green forever)

1. Every table in `public` is either in `SYSTEM_TABLES` or has a `NOT NULL owner_id`,
   `relrowsecurity` and `relforcerowsecurity` true, and the `owner_isolation` policy with the
   expressions above (compared through `pg_policies`).
2. `classbeat_app` has `rolsuper` and `rolbypassrls` false, owns no relation, and has no
   `TRUNCATE`, `REFERENCES` or `TRIGGER` privilege.
3. On a test-only owned table: user A never sees, updates, deletes or inserts user B's rows;
   a session without a context sees no rows; `classbeat_app` can't disable RLS or alter the
   table.
4. The AST check on `UserContext(` construction.
5. From M1/M2: API tests that another user's IDs give 404 on every owned endpoint, and tool
   handlers reject a foreign ID from a model.

## Alternatives

- **Application filtering only**: one forgotten filter is a breach; no second layer.
- **A schema or database per tenant**: strong isolation but migrations, pooling and shared
  music analysis get much harder; RLS gives row-level isolation in one schema.
- **Session-level `SET`**: leaks across pooled connections. Transaction-local `set_config` is
  scoped to exactly one unit of work.
- **Policies keyed on the database role per user**: needs a database role per user and
  connection per user; doesn't fit a pooled web app.

## Consequences

- Every new per-user table needs `owner_id` and `enable_owner_rls` in its migration, or the
  guard test fails the PR.
- Background work must claim through a system table and then act inside a user session; it
  can't scan owned tables across users.
- Admin features need the separate `classbeat_maint` path with an audit log (M5), not a
  shortcut in the app role.
- Bulk deletes use `DELETE`, never `TRUNCATE`.
