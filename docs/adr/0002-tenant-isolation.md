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
    connections: frozenset[tuple[str, UUID]] = frozenset()  # (provider, credential row id)
```

- Lives in `classbeat.context`. Immutable, request- or job-scoped, never stored globally.
  `connections` holds references to the user's credential rows, never the credentials. The
  context is **hashable** (a frozen dataclass whose fields are all hashable, which is why
  `connections` is a `frozenset` of pairs and not a mapping); a test checks `hash(ctx)`.
- Built **only** by two issuers: `classbeat.auth` from the authenticated server-side session
  (M1) and `classbeat.jobs` from the job row the worker claimed (M3a). Until those exist, only
  tests build one.
- Two layers keep other code from forging one, and each does a different thing:
  - **A runtime barrier**: construction requires a module-private issuer token (a keyword-only
    `issuer` argument of the class's own `__init__`, a frozen dataclass with `init=False`; it
    is compared by identity before any field is set, and a repeated `ctx.__init__(...)` is
    refused too) that only `classbeat.auth` and
    `classbeat.jobs` obtain (tests use a test-only helper). `UserContext(...)`,
    `type(ctx)(...)`, `ctx.__class__(...)` and `dataclasses.replace(ctx, ...)` raise without it.
  - **An AST guard test** scans the source outside those two packages and the tests. It fails
    on any reference to `UserContext` other than a type annotation or an import used only for
    annotations (calls, also through an alias, and subclassing are refused), on any reference
    to the token, on `Depends(UserContext)` and on a bare `Depends()` for a `UserContext`
    parameter (default value or inside `Annotated[...]`; `Depends(some_dependency)` is fine),
    and, in any module whether or not it names `UserContext`, on
    `dataclasses.replace`, `object.__setattr__`, `object.__new__`, `x.__class__(...)` and
    `type(x)(...)`.

  These stop accidental misuse and make misuse visible in review. Deliberately malicious code
  inside our own process is out of scope for runtime checks and is caught by the review gate;
  examples: `object.__setattr__` on a frozen instance, `object.__new__` plus slot descriptors,
  `ctypes`, patching `classbeat.context` itself. What the runtime barrier does cover: building
  or copying without the token, re-calling `ctx.__init__` on an existing context (refused
  before any field changes), pickling (`__reduce__` and `__setstate__` refuse), and
  `require_context(ctx)`, which every function that opens database access with a context calls
  (today `user_session`) and which accepts the exact class only (no look-alike with a
  `user_id`, no subclass instance, no UUID, no dict). Pydantic, and so FastAPI, would build a
  real `UserContext` from request data without calling `__init__`; `UserContext` therefore
  defines `__get_pydantic_core_schema__` to raise, and a model field, `TypeAdapter` or route
  parameter of that type fails at definition time. API handlers get the context through one
  FastAPI dependency (`Depends(...)`); they never take a user ID from a path, query, body,
  header or model output.
- Every service and repository function that touches owned data takes `ctx: UserContext`
  explicitly. IDs from the browser or a model are looked up *within* the owner's rows; not
  found and not owned are the same answer (404).

### 2. Owned tables

Every table holding per-user data, the job queue and job events included (roadmap §1), is
**owned**:

- `owner_id uuid NOT NULL`, referencing `users(id)` once `users` exists (M1; before that only
  test tables are owned).
- `id uuid` primary key with a **server-generated** default; owned rows never take an `id`
  from the client or a model. The app role can still insert an explicit `id` (the grants
  allow `INSERT`); that is accepted, because ids are random UUIDs and carry no authority,
  and every lookup is owner-scoped anyway.
- `UNIQUE (owner_id, id)` (alongside the primary key on `id`), so children can reference it.
  The primary key on `id` and this constraint are the only unique constraints without the
  owner rule below.
- **Every other unique constraint or unique index includes `owner_id`.** Uniqueness is checked
  without RLS, so a global `UNIQUE (name)` would let user A learn which of B's values exist and
  claim one first.
- **Foreign keys between owned tables include the owner**:
  `FOREIGN KEY (owner_id, class_id) REFERENCES classes (owner_id, id)`. Postgres checks
  foreign keys without RLS, so a plain `class_id REFERENCES classes(id)` would let user A
  point at user B's row, learn which of B's IDs exist from the error, and block B's deletes.
  With the owner in the key, a child can only reference its own owner's rows.
- **`ON DELETE` on an owner-inclusive key** is `CASCADE`, `RESTRICT` (or the default `NO
  ACTION`), or `SET NULL (<x>_id)` with the column list; never a bare `SET NULL`, which would
  null `owner_id` too.
- **No foreign key from a system table into an owned table** (§3): it would be checked
  without RLS and let system code see or block owned rows.
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
- Owned tables live in the `public` schema, neither partitioned nor in an inheritance
  hierarchy (a query on a parent reads its children's rows under the parent's policies only);
  anything else needs an ADR.

### 3. System tables

Tables that are not per-user, or that must be read before a user is known, are **system
tables**, listed in `classbeat.db.SYSTEM_TABLES` with a one-line reason each, and they **have
no `owner_id` column**. Examples: users, identities and sessions read at sign-in (M1, accessed
only through `classbeat.auth`), and shared music analysis (§6, reused only after an access
check with the requester's credentials).

Cross-user operations on owned tables go through narrow **`SECURITY DEFINER` functions**, not
broad table access:

- Each function is owned by a dedicated `NOLOGIN` role with `BYPASSRLS` and only the table
  privileges it needs, sets `search_path = pg_catalog, public, pg_temp` (amended by
  [ADR 0005 §3](0005-jobs-and-leases.md): `pg_catalog, pg_temp`, `public` left out) and
  schema-qualifies what it touches, takes and returns the minimum (for the job queue: claim
  the next job and return its `id`, `owner_id` and `lease_version`), and `classbeat_app` gets
  only `EXECUTE` on it.
- Handing a function to its owner role needs membership in that role, and membership would
  give `classbeat_owner` `BYPASSRLS` through `SET ROLE`. So the superuser runs that step
  (a privileged migration step the M3a ADR defines), and `classbeat_owner` is never a member
  of a `BYPASSRLS` role.
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

`UPDATE` and `DELETE` let a role take any table lock (`LOCK TABLE … ACCESS EXCLUSIVE`), and
Postgres has no way to grant them without it. A stuck lock is a denial-of-service risk, not a
data leak. `statement_timeout` (30 s), `lock_timeout` (5 s) and
`idle_in_transaction_session_timeout` (60 s) are **role defaults** of `classbeat_app`
(`ALTER ROLE … SET`), so every connection has them, not only sessions that set them. Injected
SQL could lower them, even persistently: a role may run `ALTER ROLE classbeat_app SET
statement_timeout = 0` for itself and Postgres cannot forbid it. So this limits accidents,
not attacks; the change would persist until repaired, and is **detected** two ways: web and
worker check at startup (and on every new connection) that the role's `pg_db_role_setting`
rows are exactly these three at role level, and refuse to start otherwise, and guard 2 checks
the same. T26 in the threat model covers the injection path.

- `PUBLIC` also loses `CONNECT` on the `postgres` and `template1` databases, so the app role
  connects to its own database only.
- `PUBLIC` loses `CONNECT` and `TEMP` on the database and `USAGE` and `CREATE` on schema
  `public`, and
  `ALTER DEFAULT PRIVILEGES FOR ROLE classbeat_owner REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC`
  stops new functions being callable by everyone. `classbeat_owner`, `classbeat_app` and
  (from M5) `classbeat_maint` get `CONNECT` explicitly; `classbeat_owner` and `classbeat_app`
  get `USAGE` on `public` explicitly (the owner also `CREATE`).
- Compose creates the roles in `docker/postgres/init/` from secret files
  (`postgres_password`, `database_owner_password`, `database_password`).
- Web and worker use `CLASSBEAT_DATABASE_URL` + `database_password`; `migrate` uses
  `CLASSBEAT_MIGRATIONS_DATABASE_URL` + `database_owner_password`.
- **Web and worker refuse to start** if their database role is a superuser, has `BYPASSRLS`,
  is a member of such a role or of `pg_execute_server_program`, `pg_read_server_files` or
  `pg_write_server_files`, or owns any table, schema or the database. The check runs at startup
  and again on every new pooled connection, so a database that was down at startup cannot
  let a privileged connection through later. A refused login (wrong password or role) is a
  fatal startup error with a clear message and no credentials in it, not "database down". There
  is no override.
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
  `session.commit()` or `session.rollback()` inside it raises, because a new transaction would
  run without the setting. Savepoints (`begin_nested()`) are allowed, and `app.user_id` survives
  a rolled-back savepoint. Ending the transaction through another path (`run_sync`, the
  connection's own `rollback()`, a raw `ROLLBACK`) is not intercepted, and fails closed: the
  setting is gone, so later statements see no rows and cannot write owned rows. Committing
  through the connection or a raw `COMMIT` would commit partial work; that is forbidden by
  convention and the review gate, not enforced.
- Never a session-level `SET app.user_id`. The pool resets connections on return with a
  rollback followed by `DISCARD ALL` (the engine's `reset` event, `pool_reset_on_return=None`),
  which also drops `WITH HOLD` cursors, temp tables, prepared statements, advisory locks and
  session settings; a rollback alone leaves those, so a held cursor opened as user A could be
  fetched by user B on the same connection. If the reset fails, the connection is
  invalidated. A rollback does not undo a session-level `SET` that was committed either, so
  every pool checkout also checks that `app.user_id` is unset and the three timeouts equal the
  role defaults, and invalidates the connection otherwise.
- One transaction per `user_session`; a function needing two users' data is a design error.
- Long-lived work holds no session: a server-sent events stream opens a short `user_session`
  per read. `LISTEN` uses its own dedicated connection, which never runs queries on owned
  tables, and `NOTIFY` payloads carry only IDs (any listener on the database can read them).

### 6. Guard tests (from #5, kept green forever)

1. Every table in `public` is either in `SYSTEM_TABLES` (and then has no `owner_id`), or is
   owned: `owner_id` `NOT NULL`, `relrowsecurity` and `relforcerowsecurity` true, exactly one
   permissive policy, named `owner_isolation`, for `ALL` commands and `PUBLIC`, with the
   expressions above (compared through `pg_policies`: `permissive`, `cmd`, `roles`, `qual`,
   `with_check`), any other policy restrictive, `UNIQUE (owner_id, id)`, every other unique
   constraint or index including `owner_id`, and every foreign key to another owned table
   mapping `owner_id` to the parent's `owner_id` (column pairs compared, so a swapped key
   fails), with `ON DELETE` as in §2, `id` of type `uuid` with the server default
   `gen_random_uuid()`. No table in `public` is partitioned or takes part in inheritance
   (any `pg_inherits` row naming one fails), no foreign key goes from a system table into an
   owned table, no exclusion constraint on an owned table lacks `owner_id WITH =`, and
   `public` holds no views.
2. `classbeat_app`'s privileges equal the allowlist in §4 (checked as an allowlist against
   `pg_roles`, `pg_auth_members`, `information_schema` and `aclexplode`, so a new privilege
   kind fails the test), and nothing is granted to `PUBLIC` beyond Postgres' built-in
   catalog access (`pg_catalog`, `information_schema`, the `plpgsql` and `sql` languages). The
   ACLs checked are those on the database, schema `public` and its tables, sequences,
   columns and functions, the default privileges, `CONNECT` on every other database, and the
   `classbeat_app` role settings (exactly the three timeouts at role level, nothing per
   database and nothing else, such as `session_replication_role` or `search_path`). Other ACL
   kinds (large objects, types) are out of scope. `classbeat_owner` has `rolbypassrls` false and is a member of no
   `BYPASSRLS` role.
3. On test-only owned parent and child tables: user A never sees, updates, deletes or inserts
   user B's rows; A can't create a child pointing at B's parent; a session without a context
   sees no rows; `classbeat_app` can't disable RLS or alter the table; a global-looking
   unique value of B's can't be detected or blocked by A.
4. A pooled connection reused after a `user_session` carries no `app.user_id`; a `WITH HOLD`
   cursor opened by one user cannot be fetched by the next user of the connection; `commit()`
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
