# Backup and restore

M6 (issue #338). How to back up the classbeat database, restore it into a **fresh** compose stack,
and prove that it works (the drill). Nothing here touches the VPS: no agent has access to it, so the
schedule below is a recommendation for the owner to install at deploy time, not something that runs.

## What is in a backup, and what is not

| In the backup (`scripts/backup.sh`) | Not in it, and why |
|---|---|
| The database `classbeat`: schema, data, grants, row level security policies, the queue and admin functions, `alembic_version` | `secrets/` (database, app and KEK files): a backup that held the keys would make the encryption pointless |
| Users, password hashes (Argon2), session and token hashes, auth events, classes, jobs, usage, plans, admin audit | The roles and their passwords (cluster-level, not dumped). A fresh stack creates them from its own `secrets/` |
| `connections` and TOTP secrets as **ciphertext** with their wrapped data keys and `key_id`; `connection_keks` (check values only) | The KEK files (`connection_kek`, `connection_kek_<id>`) and `app_secret_key` |
| | Mailpit's mailbox, model API keys, container images |

Consequences:

- A backup alone cannot read any provider token. **A restore is only useful together with the KEK
  file of every `key_id` that appears in the backup** (`SELECT key_id, count(*) FROM connections
  GROUP BY 1`, plus TOTP rows). Without it the rows stay, the stack starts under a new KEK id, and
  affected users must reconnect (`key_unavailable`).
- The backup is still sensitive: password hashes, hashed session tokens, class content, email
  addresses. Restored session tokens are valid again, so after restoring from an incident, revoke
  sessions (`DELETE FROM sessions` as the superuser, before web starts). Keep backups encrypted and off the host that runs the stack.
- The wipe after a suspected KEK compromise (ADR 0004 section 2) does not clean old backups: a
  backup taken before it plus the leaked KEK still reads the old connections. Destroy those backups
  too.

## Where the keys live

- On the host: `secrets/connection_kek` (current KEK, id in `.env` as `CONNECTION_KEK_ID`) and, after
  a rotation, `secrets/connection_kek_<old id>` until `keys rewrap` reports zero rows for it.
- **Off the host, apart from the backups:** a copy of every KEK file that any retained backup uses,
  in the owner's password manager or another secret store. ADR 0004 section 2: keep the old KEK
  until every backup taken before the rotation has aged out of retention, then destroy it. The same
  applies to `database_*_password` and `app_secret_key` (a restore needs the stack's secrets to
  exist; they need not equal the old ones, except the KEK and `app_secret_key`: TOTP secrets in the
  v2 form use the KEK, v1 rows and device and CSRF keys derive from `app_secret_key`).

## Taking a backup

```bash
scripts/backup.sh -o /srv/classbeat-backups --keep 14 --gpg-recipient KEYID
```

`pg_dump --format=custom` runs in the `db` container as the superuser over the local socket (no
password on a command line). The superuser is required: every owned table has forced row level
security, and a dump as `classbeat_owner` would silently skip rows. The script checks that
`pg_restore --list` can read the archive, writes mode 600 and a `.sha256`, and prints the file name.
Without `--gpg-recipient` it warns that the file is not encrypted. With it, `pg_dump` is piped
straight into `gpg`: no plaintext dump touches the disk (the encrypted archive is then not read back,
that needs the private key; the drill and a monthly restore are its test). `--keep N` (a positive
whole number, checked before anything is written or deleted) deletes older files named exactly
`classbeat-<UTC timestamp>.dump[.gpg]` in the directory (anything else is left alone), never the
backup it just made. Stack selection is the usual compose one
(`COMPOSE_PROJECT_NAME`, `.env`, `COMPOSE_FILE`).

**Recommended schedule and retention** (to be installed on the VPS by the owner): daily at 03:00
local (`cron` or a systemd timer calling the script), keep 14 dailies, plus copy the newest backup
weekly to storage off the host and keep 8 of those. Before every migration-bearing deploy take one
by hand. The restore drill below runs after each change to the scripts, the schema tooling or the
Postgres image, and the owner should run it by hand against a real backup (see below) monthly.

## Restoring into a fresh stack

```bash
export COMPOSE_PROJECT_NAME=classbeat-restore     # a NEW name: its own volume and ports
# put the secrets this stack needs in secrets/, KEK files included (see above)
scripts/restore.sh /srv/classbeat-backups/classbeat-20261031T030000Z.dump.gpg
# check the data, then:
docker compose up -d --wait web worker
```

`restore.sh` verifies the checksum, decrypts a `.gpg` file with your gpg key (to a mode 600 temporary
file, removed on exit), **refuses the default project `classbeat` and a project whose volume
`<project>_db-data` already exists** (before it starts anything), starts `db`, and only then **refuses a database that already holds
tables** (it has started that project's `db`, but wrote nothing), runs `migrate` and `db-functions` (the schema, plus the
`classbeat_queue` and `classbeat_maint` roles the dump refers to), runs `pg_restore --clean
--if-exists --single-transaction --exit-on-error`, then runs `migrate` (a backup from an older
release is upgraded to the image's head; a backup newer than the image fails loudly) and
`db-functions` again. If anything fails nothing is restored: `docker compose down -v` and start over.
Web and worker refuse to start when `connection_kek` is not the key whose check value is in the
restored `connection_keks` (a wrong key under the same id), so a wrong key can never silently run.

To restore into the live project, stop the stack, take a backup of the broken state first, and
`docker compose down -v` it yourself; the scripts never delete a volume.

## The drill

```bash
scripts/backup-restore-drill.sh [NAME]     # about 3 minutes with a cold image build
```

It creates `classbeat-drill-NAME-src` and `-dst` (own volumes, random free ports on 127.0.0.1;
it touches no other project and removes both, their images and its temp files at the end, also on
failure):

1. Source stack up; two users with passwords and an encrypted MOWL connection each; one sign-in.
2. A fingerprint of the database: row counts of every table, table owners, ACLs, RLS flags and
   the full policy definitions (command, permissive or restrictive, roles, USING and WITH CHECK), function owners, digests and ACLs, the `classbeat%` roles' attributes, migration head.
3. `scripts/backup.sh`; the decoded dump (`pg_restore -f -`: the archive compresses its data, so the
   raw bytes cannot be searched) must not contain the seeded token or password as text or as hex (a
   bytea column such as `connections.secret` is printed as hex), and that check is first shown to
   find a token planted in a text column and in a bytea column (both hex cases are searched); `--keep` is exercised (bad values refused with
   nothing deleted, a valid one prunes old files only); the source stack and volume are destroyed.
4. `scripts/restore.sh` into the fresh `-dst` project; the fingerprint must be identical.
5. Web and worker start (the KEK check passes); migrations are at head; the right key decrypts both
   connections to the seeded tokens; with no user a session sees 0 connection rows and each user
   sees only their own; both users sign in over HTTP and a wrong password gets 401; writes that must be refused by RLS (moving a row to another owner,
   inserting a row for another owner) are refused.
6. Wrong keys: a different key under the same id is refused at startup (`KekError`); a different key
   under another id starts but gets `key_unavailable` for the old rows; the same wrong key used
   without the startup check fails authentication (`decrypt_failed`). These probes call the same
   functions web and worker call at startup (`load_keyring`) rather than restarting web; a
   wrong `connection_kek` file under the right id was also checked by hand to make web refuse to start.

CI runs it in the `e2e` job (on any change to the stack, the scripts or `docker/`), as a step before
Playwright. It adds about three minutes to a job that already runs for many; the second image build
is a layer-cache hit.

**What the drill does not prove:** that your real backup restores (it uses a seeded database; run
`scripts/restore.sh` on a copy of a real one at least monthly, into a throwaway project, and
`down -v` it afterwards); the `--gpg-recipient` path is a thin wrapper around `gpg` and is not part
of the drill; restoring a dump from an older release is supported by the script (migrate runs after
the restore) but not exercised by the drill; the VPS, its disk, the schedule and the off-host copy
do not exist yet.
