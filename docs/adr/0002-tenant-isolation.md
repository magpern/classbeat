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
    connections: Mapping[str, UUID] = MappingProxyType({})  # provider → credential row id
```

- Lives in `classbeat.context`. Immutable, request- or job-scoped, never stored globally.
  `connections` holds references to the user's credential rows, never the credentials.
- Built **only** by two issuers: `classbeat.auth` from the authenticated server-side session
  (M1) and `classbeat.jobs` from the job row the worker claimed (M3a). Until those exist, only
  tests build one.
- A guard test scans the source (AST) outside those two packages and the tests, and fails on
  any reference to `UserContext` other than a type annotation or an import used only for
  annotations: calls (including through an alias), `dataclasses.replace`, `functools.partial`
  and subclassing are all refused. API handlers get the context through one FastAPI
  dependency; they never take a user ID from a path, query, body, header or model output.
- Every service and repository function that touches owned data takes `ctx: UserContext`
  explicitly. IDs from the browser or a model are looked up *within* the owner's rows; not
  found and not owned are the same answer (404).

### 2. Owned tables

Every table holding per-user data, the job queue and job events included (roadmap §1), is
**owned**:

- `owner_id uuid NOT NULL`, referencing `users(id)` once `users` exists (M1; before that only
  test tables are owned).
- `UNIQUE (owner_id, id)` (alongside the primary key on `id`), so children can reference it.
- **Foreign keys between owned tables include the owner**:
  `FOREIGN KEY (owner_id, class_id) REFERENCES classes (owner_id, id)`. Postgres checks
  foreign keys without RLS, so a plain `class_id REFERENCES classes(id)` would let user A
  point at user B's row, learn which of B's IDs exist from the error, and block B's deletes.
  With the owner in the key, a child can only reference its own owner's rows.
- RLS, applied by the migration helper `classbeat.db.rls.enable_owner_rls(table)`:

```sql
ALTER TABLE <t> ENABLE ROW LEVEL SECURITY;
ALTER TABLE <t> FORCE ROW LEVEL SECURITY;
CREATE POLICY owner_isolation ON <t>
  AS PERMISSIVE FOR ALL TO PUBLIC
  USING      (owner_id = NULLIF(current_setting('app.user_id', true), '')::uuid)
  WITH CHECK (owner_id = NULLIF(current_setting('app.user_id', true), '')::uuid);
```

- `current_setting(…, true)` is NULL before the setting is first used in a session and `''`
  after a transaction-local value has ended, so `NULLIF` turns both into NULL: **no setting,
  no rows**, never an error and never everything.
- `FORCE` makes the policy apply to the table owner too.
- **`owner_isolation` is the only permissive policy on an owned table.** Permissive policies
  combine with OR, so any second permissive policy (even `USING (true)`) would widen access.
  Further policies must be `AS RESTRICTIVE`, which can only narrow it.
- Repositories still filter `owner_id = ctx.user_id` themselves; RLS is the second layer, not
  the only one.
- Owned tables live in the `public` schema and are not partitioned; anything else needs an ADR.

### 3. System tables

Tables that are not per-user, or that must be read before a user is known, are **system
tables**, listed in `classbeat.db.SYSTEM_TABLES` with a one-line reason each, and they **have
no `owner_id` column**. Examples: users, identities and sessions read at sign-in (M1, accessed
only through `classbeat.auth`), and shared music analysis (§6, reused only after an access
check with the requester's credentials).

Cross-user operations on owned tables go through narrow **`SECURITY DEFINER` functions**, not
broad table access:

- Each function is owned by a dedicated `NOLOGIN` role with `BYPASSRLS` and only the table
  privileges it needs, has a fixed `search_path`, takes and returns the minimum (for the job
  queue: claim the next job and return its `id`, `owner_id` and `lease_version`), and
  `classbeat_app` gets only `EXECUTE` on it.
- After a claim, the worker builds the `UserContext` from the returned owner and reads the
  job's data in a user session.
- The milestone that adds such a function defines it in its own ADR (M3a: claim and reaper).

### 4. Database roles

| Role | Login | Used by | Rights |
|---|---|---|---|
| `postgres` (superuser) | yes | the db container's init script only | creates the roles and the database |
| `classbeat_owner` | yes | `migrate` (Alembic) | `NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE`; owns the database, the `public` schema and every table; never used by web or worker |
| `classbeat_app` | yes | web and worker | `NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOINHERIT`, member of no role; owns nothing; exactly the grants below |
| function owners (for example `classbeat_queue`) | no | `SECURITY DEFINER` functions (M3a) | `BYPASSRLS`, only the table privileges their functions need |
| `classbeat_maint` | yes | audited admin and maintenance paths (M5) | `BYPASSRLS`; never used in a normal user's request path; every use writes an audit row |

`classbeat_app`'s grants are an **allowlist**: `CONNECT` on the database; `USAGE` on `public`;
`SELECT, INSERT, UPDATE, DELETE` on tables and `USAGE, SELECT` on sequences, through
`ALTER DEFAULT PRIVILEGES FOR ROLE classbeat_owner IN SCHEMA public`; `EXECUTE` on the
`SECURITY DEFINER` functions meant for it. Nothing else: in particular no `TRUNCATE` (it
ignores RLS), `REFERENCES`, `TRIGGER`, `MAINTAIN` (Postgres 17), `CREATE` or `TEMP`.

- `PUBLIC` loses `CONNECT` and `TEMP` on the database and `CREATE` on the schema.
- Compose creates the roles in `docker/postgres/init/` from secret files
  (`postgres_password`, `database_owner_password`, `database_password`).
- Web and worker use `CLASSBEAT_DATABASE_URL` + `database_password`; `migrate` uses
  `CLASSBEAT_MIGRATIONS_DATABASE_URL` + `database_owner_password`.
- **Web and worker refuse to start** if their database role is a superuser, has `BYPASSRLS`,
  or owns any table. A setting allows this only in `environment=dev` until #5 lands.
- Data migrations on owned tables see no rows under `FORCE` (an `UPDATE` reports 0 rows). They
  either run per owner with `app.user_id` set, or as `classbeat_maint` with an audit row; the
  migration states which.

### 5. Sessions

Module `classbeat.db.session`:

```python
async with user_session(sessionmaker, ctx) as session:
    ...  # owned and system tables, as ctx.user_id
async with system_session(sessionmaker) as session:
    ...  # system tables only; owned tables return no rows
```

- `user_session` begins a transaction, runs
  `SELECT set_config('app.user_id', :user_id, true)` (transaction-local), yields the session,
  commits on success and rolls back on an exception. **It owns the transaction**: calling
  `commit()` or `rollback()` inside it raises, because a new transaction would run without
  the setting.
- Never a session-level `SET app.user_id`. The pool resets connections on return (rollback).
- One transaction per `user_session`; a function needing two users' data is a design error.
- Long-lived work holds no session: a server-sent events stream opens a short `user_session`
  per read. `LISTEN` uses its own dedicated connection, which never runs queries on owned
  tables, and `NOTIFY` payloads carry only IDs (any listener on the database can read them).

### 6. Guard tests (from #5, kept green forever)

1. Every table in `public` is either in `SYSTEM_TABLES` (and then has no `owner_id`), or is
   owned: `owner_id` `NOT NULL`, `relrowsecurity` and `relforcerowsecurity` true, exactly one
   permissive policy, named `owner_isolation`, with the expressions above (compared through
   `pg_policies`), any other policy restrictive, `UNIQUE (owner_id, id)`, and every foreign
   key to another owned table including `owner_id`. No owned table is partitioned.
2. `classbeat_app`'s privileges equal the allowlist in §4 (checked as an allowlist against
   `pg_roles`, `pg_auth_members`, `information_schema` and `aclexplode`, so a new privilege
   kind fails the test); `classbeat_owner` has `rolbypassrls` false.
3. On test-only owned parent and child tables: user A never sees, updates, deletes or inserts
   user B's rows; A can't create a child pointing at B's parent; a session without a context
   sees no rows; `classbeat_app` can't disable RLS, alter or lock the table.
4. A pooled connection reused after a `user_session` carries no `app.user_id`; `commit()`
   inside `user_session` raises.
5. The AST check of §1.
6. The startup refusal of §4.
7. From M1/M2: API tests that another user's IDs give 404 on every owned endpoint, and tool
   handlers reject a foreign ID from a model.

In CI (`CI=true`) a missing test database fails these tests instead of skipping them.

## Alternatives

- **Application filtering only**: one forgotten filter is a breach; no second layer.
- **A schema or database per tenant**: strong isolation but migrations, pooling and shared
  music analysis get much harder; RLS gives row-level isolation in one schema.
- **Session-level `SET`**: leaks across pooled connections. Transaction-local `set_config` is
  scoped to exactly one unit of work.
- **Policies keyed on a database role per user**: needs a role and connection per user;
  doesn't fit a pooled web app.
- **A system copy of the job queue without RLS**: two sources of truth for job state;
  a narrow `SECURITY DEFINER` claim keeps jobs owned.

## Consequences

- Every new per-user table needs `owner_id`, `UNIQUE (owner_id, id)`, owner-inclusive foreign
  keys and `enable_owner_rls`, or the guard test fails the PR.
- Background work claims through a `SECURITY DEFINER` function and then acts inside a user
  session; it can't scan owned tables across users.
- Admin features need the separate `classbeat_maint` path with an audit log (M5).
- Bulk deletes use `DELETE`, never `TRUNCATE`; data migrations say how they reach owned rows.
- The app role can still set `app.user_id` itself, so SQL injection would defeat both layers:
  all SQL uses bound parameters (threat model T26).
