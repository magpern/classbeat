# ADR 0014: Account data export and account deletion (M6, GDPR)

Status: Proposed (2026-10-31, issue #337; a fresh Opus Reviewer reviews it in its PR)

## Context

The roadmap's M6 "done when" includes "GDPR export/delete verified" and the account page lists
Export and Delete. Before this ADR:

- Deletion existed (`DELETE /api/auth/account`, ADR 0003 section 6): recent authentication, then
  `DELETE FROM users` with the foreign keys cascading. It had no server-side confirmation (the
  typed email was only a front-end check), did not end running jobs first, and left the user's
  `auth_events` (with their IP address and detail) in place for the 30-day purge, unlinked by
  `ON DELETE SET NULL` but not anonymous.
- Retention (ADR 0007 section 11, guard 10) already proves that every *owned* table has a deletion
  path. Nothing covered the tables without `owner_id` (the account tables, plans, the audit log),
  and nothing could export.
- There was no export. "Your data" on the account page had a disabled button.

## Decision

### 1. What is "the user's data"

Every row reachable from the `users` row, and every row that names the user:

| Where | Rows | On delete |
|---|---|---|
| Account tables (`classbeat.auth`) | `users`, `identities`, `sessions`, `known_devices`, `auth_tokens`, `oauth_flows`, `mfa_totp`, `mfa_recovery_codes` | cascade |
| Security events | `auth_events` (kind, IP, detail) | **cascade** (migration 0015; was `SET NULL`) |
| Owned tables | every table with `owner_id`: classes, revisions, messages, saved workouts, jobs and events, external operations, MOWL links, usage (`agent_runs`), connections (ciphertext), Spotify flows, pool and playlists, entitlements, redemptions, the credit ledger, quota periods | cascade (ADR 0007 section 11) |
| Links that only name a user | `invites.created_by/used_by`, `plans.updated_by`, `redeem_codes.created_by`, `entitlements.granted_by`, `admin_audit.actor_id/target_user_id` | `SET NULL`: the row belongs to the operator or the invite, and holds no personal data once the id is null |
| User ids without a foreign key | `entitlements.revoked_by`, `credit_ledger.refunded_by`, `app_settings.updated_by` | stay: a bare UUID that names an admin who acted, and leads to nobody once the admin's account is gone |

**Retained by design (see section 5):** `admin_audit.actor_email` and the bare id in
`admin_audit.target_id`. `admin_audit.actor_email`: The audit log is append-only and refuses updates
and deletes (ADR 0011 section 5, T92); an admin's address stays beside what they did, for
accountability (GDPR Art. 6(1)(f)). A user who never acted as an admin has no such row; the log
records what an operator did *to* a user by the user's id only, which becomes null.

Never shipped or stored by us and therefore not deleted by us: workouts saved to the user's MOWL
account and playlists made in their Spotify account. They are the user's, in those services; the
delete dialog says so (as the class delete does).

### 2. The export

`POST /api/account/export` returns one JSON document (`Content-Disposition: attachment`,
`classbeat-export-<date>.json`, `Cache-Control: no-store`). Needs recent authentication and, once
TOTP is confirmed, a step-up (`require_recent_auth`, ADR 0003 section 6): a stolen session cookie
cannot pull the user's data out. It is a **POST** (#349): the auth middleware's CSRF-token and
origin checks refuse a request from another site, so a link, image or form elsewhere cannot use the
10-minute recent-authentication window to burn the allowance. Limited to 5 per user and 20 per IP
and hour (every request counts). Format `classbeat-export` version 1:

- `account` (profile: id, email, role, dates, whether there is a password), `sign_in_methods`
  (provider, provider subject, address at linking, date), `two_factor` (on or off, confirmed date,
  unused recovery codes: a count), `sessions` (dates, user agent, IP; no token), `known_devices`
  (dates), `security_events` (kind, IP, time, detail);
- `connections`: one entry per provider with **status only** (status, last error category, dates);
- one array per owned table (`classes`, `class_revisions` with the song lists, `class_messages`,
  `saved_workouts`, `class_playlists`, `mowl_playlist_links`, `spotify_pool`, `jobs`, `job_events`,
  `external_operations`, `agent_runs` (usage), `entitlements`, `code_redemptions`, `credit_ledger`,
  `quota_periods`), each row as the database holds it (`to_jsonb`) **minus** the columns the
  registry omits: idempotency keys, request and content hashes, lease and worker fields, the ids of
  the admins who granted, revoked or refunded;
- `not_included`: the plain-language list of what is left out.

**Built from owner-scoped rows only.** The export runs in the user's own `user_session` (row-level
security) and every statement also filters on `owner_id` (or the account tables on the user's id),
so a lost policy still shows nobody else's row. `tests/test_account_export.py` proves both: two
users with full data, the file of one holds no id, address or row of the other, and the same
function run on a superuser session (RLS out of the picture) does the same.

**The registry is the contract.** `classbeat.account.registry` has one entry per owned table: a
`Section` (table, fixed order, omitted columns) or a reason in `NOT_EXPORTED` (`connections`:
ciphertext and the Client ID; `spotify_oauth_flows`: a one-time verifier). The guard
(`tests/gdpr_guard.py`, run on the migrated schema) fails when an owned table is in neither, when
an entry names a missing table or column, and when a `bytea` column (every hash, key and ciphertext
is one) of an exported table is not omitted. A new column with content is therefore exported by
default, and a new secret-like column must be decided in the PR that adds it.

**Not in the export: the admin audit log.** `admin_audit` has no grant for the app role (ADR 0011
section 5); reading it from a user's request would give the web role a new privilege for little
gain. What an operator did to the account is told to the user on request by the operator. The
export says so in `not_included`. (Alternative below.)

**Account tables, column by column.** `users`, `identities`, `sessions`, `known_devices`,
`mfa_totp`, `mfa_recovery_codes` and `auth_events` have no registry of rows but of columns:
`classbeat.auth.export.INCLUDE` lists what each select reads (the selects are built from it),
`EXCLUDE` the rest with a reason (`password_hash`, `token_hash`, `secret_ciphertext`, the wrapped
keys and so on), and `NOT_EXPORTED` a reason for every other system table.
`tests/test_account_export_schema.py` reads the live schema and fails when a column or a system
table is in neither, so a new column on those tables cannot be silently omitted, and a secret in a
`text` column (not only `bytea`) cannot slip in without an `INCLUDE` line a reviewer reads.

The document is built in memory in one transaction: a user holds tens of classes, not millions of
rows. Each table is read at read-committed within it; a job finishing during the export may show
in one table and not yet in another. Acceptable for a copy of one's own data.

### 3. The deletion

`POST /api/account/delete {"confirm_email": "..."}` (the old `DELETE /api/auth/account` is
removed, so no path skips the confirmation). Needs recent authentication and the step-up, like the
export. In order:

1. **Confirmation.** The typed address, normalized (ADR 0003 section 6), must equal the account's;
   otherwise 400 `confirmation_mismatch` and nothing changes. Checked on the server, not only in
   the dialog.
2. **Open jobs end** (the operator's delete from the Users screen takes the same step,
   `erase.end_jobs_of`, after its checks and before its row lock, #349). Under the owner lock, every `queued` and `waiting_for_approval` job is
   cancelled and every `running` job gets `cancel_requested_at` (the same transitions as the user's
   Cancel, `classbeat.jobs.user.cancel`). The request waits up to 15 seconds for running jobs to
   stop at a checkpoint.
3. **Credits settle.** Every ended job whose credit is still `reserved` is settled by
   `classbeat.jobs.credits.settle_one` with the metered ledger (`released` for a cancelled job,
   `committed` for a completed one), exactly as the worker's settle step would. A job still running
   after the wait keeps its reservation; it goes with the ledger in step 4 (nothing is owed: the
   ledger belongs to the account).
4. **The row goes.** Under the owner lock again (no job can be created between steps 2 and 4),
   `users` is deleted and the cascades do the rest; one `account_deleted` event is written **with
   no user id and no address**. A job still running fails its next fenced write (T53) and ends;
   nothing it writes survives, because its rows are gone.
5. The session cookie is cleared.

No request goes to MOWL, Spotify or a model (the test counts at the fakes). A write that was in
flight to a provider when the account went is the user's to see in their own account, as with a
class delete.

### 4. Verification: from the schema, not a list

`classbeat.account.coverage` lists every foreign key to `users` and every `uuid` column named like
a user reference (`*_by`, `*user_id`, `owner_id`, `actor_id`) with its action. The guard reads
`pg_constraint` and `information_schema` of the migrated database and fails when a column is
missing from the list (a new table without a decision), when the database's `ON DELETE` differs
from the list, and when an entry is stale. Guard 10 (retention) keeps covering the owned tables'
columns.

The behavioural test (`tests/test_account_delete.py`) plants one row in each of those tables for
two users (a full song-picking account plus identities, devices, TOTP, recovery codes, tokens,
events with the address, sessions with an IP, messages, saved workouts, a redeemed code, a grant
and two audit rows), deletes one, and then scans **every column of every table** (catalog-driven):
`uuid` columns for the user's id; `text`, `varchar`, `jsonb` and `inet` columns for the address, the
IP and the **id written as text** (a log line, an event detail). Nothing remains except the
documented retained bare id in `admin_audit.target_id` (`coverage.RETAINED`). `bytea` columns are
not scanned: they hold hashes and ciphertext, none of which carries the user's id or address in the
clear.
The other user's rows are unchanged. A scan probe proves the scan can fail (a table with no
foreign key that names the user is reported).

### 5. What the admin audit log keeps

The log is append-only (ADR 0011 section 5, T92) and the app role cannot touch it, so it is not
scrubbed at delete; instead **nothing identifying another person is written into it**:

- The Users list's search text (`user.list`) is a prefix of someone's email. It is no longer stored:
  the row keeps `q_length` only. The audit list's email filter likewise keeps `actor_email_set`.
- A grant's note is stored on the entitlement (deleted with the account), never in the audit row;
  the row says only that a note was given (`note: true`).
- The remaining identifiers are ids, not names: `target_id` (the user's id as text, kept so the log
  can say that an operator deleted or disabled account X) and `target_user_id` (nulled by the
  foreign key). After the account is deleted nothing in the database maps the id to a person.

**Retention rule.** The bare `target_id` and the `actor_email` of an admin stay for as long as the
audit log does. The log has no purge today (a decision for the M6 security review, with the
backup retention); when one is set it applies to these two fields. Rows written before #349 may
hold a search text; the beta has none in production (nothing is deployed).

## Alternatives

- **Anonymise `auth_events` instead of cascading.** Keeps abuse forensics for 30 days, but the
  remaining rows hold an IP address and `detail` (which can hold an email), so "anonymised" would
  need a scrub of both and a new privilege; they are only useful while tied to an account. Cascade
  is simpler and stronger.
- **A soft delete with a grace period.** Standard for consumer products, but the beta has no
  billing, and a grace period keeps the data the user asked us to delete. Not now.
- **Export through `classbeat_maint` including the audit log.** Gives the web process a new reason
  to hold the maint role; the audit log is the operator's. Rejected for the beta; the operator
  answers a request for it.
- **A ZIP with one file per table, or CSV.** More code and no benefit to one person reading their
  own data; JSON keeps nested fields (a class draft, a brief) intact.
- **Streaming the export.** Not needed at this size; revisit if a user can hold millions of rows.

## Consequences

- `auth_events` rows of a deleted or admin-deleted user go with the account (migration 0015);
  ADR 0003's "30-day purge" still applies to events of existing users and to `account_deleted`.
- A new owned table, or a new column that names a user, breaks CI until it has a line in the export
  registry and the coverage list: the same way guard 10 forces a retention line.
- `classbeat.account` is added to the user-session allowlist (`tests/session_scan.py`) for the
  user's own export and deletion; only its routes import it, and only the web app imports those.
- The admin's Users screen delete (`classbeat.admin.users`) uses the same `delete_account` and the
  same cascades and, since #349, ends the target's jobs and settles their credits first through
  `classbeat.account.erase`; it is the operator's action, audited. It checks the target without a
  row lock, ends the jobs, then locks and rechecks the role: the jobs' inserts take a key-share lock
  that the `FOR UPDATE` of the audited transaction would block.
  The admin package does not import the jobs code (guard 11: no advisory lock or NOTIFY on the
  admin path): `web` installs `erase.make_job_ender(...)` as `app.state.end_account_jobs` and the
  route calls that value, as the gate calls the rate limiter. The work runs in the target's own
  `user_session`s, never on the maint session. A pinned test lists the importers of
  `classbeat.account.erase` (the routes and `web`), and `account/erase.py` joins the short list of
  modules that may issue the target's bare `owner_context` (`tests/test_user_context_guard.py`).
- Residual: the export reads at read-committed; `admin_audit.actor_email` outlives an admin's
  account; user ids of acting admins stay as bare UUIDs in three columns without a foreign key;
  the bare `admin_audit.target_id` of a deleted user stays as long as the log; earlier unlinked
  `auth_events` (from before migration 0015) expire within the 30-day purge; the browser may cancel
  the download if the object URL is released right after the click (the user presses the button
  again); deleting and signing up again resets the trial and the month's quota (invites gate
  sign-up today; a closed or public sign-up needs a rule before it opens).

## Status

Proposed. Implemented in #337.
