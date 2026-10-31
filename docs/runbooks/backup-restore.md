# Backup and restore

M6 (issues #338 and #355). How to back up the classbeat database, restore it into a **fresh** compose stack,
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

## One-time setup: the keys of the backup

A backup is **encrypted and signed**, and a restore only trusts an archive signed by a key you name.
Two different jobs, two different keys (SR-06, #355):

| Key | Job | Lives |
|---|---|---|
| Recipient key R (gpg) | Encrypts the archive. Only its private half can read a backup | Public half on the VPS; **private half off the VPS** (password manager, a second machine) |
| Signing key S (gpg) | Signs the archive; the restore host pins its fingerprint | Private half on the VPS, usable without a prompt for cron; public half and **fingerprint** on every restore host |

```bash
gpg --quick-generate-key "classbeat-backup-recipient" default default never   # on a trusted machine
gpg --quick-generate-key "classbeat-backup-signer" default default never      # on the VPS (no passphrase for cron)
gpg --list-keys --with-colons classbeat-backup-signer | awk -F: '/^fpr:/ { print $10; exit }'   # the primary fingerprint
```

Fingerprints are **full 40-digit primary-key fingerprints**, passed as flags or as
`BACKUP_GPG_RECIPIENT`, `BACKUP_GPG_SIGNER` and `RESTORE_EXPECT_SIGNER`. `restore.sh` accepts a
signature only as made by the pinned **primary** key (or a subkey of it); pinning a signing
subkey's own fingerprint is refused. Names, short ids and
`--trust-model` games cannot pick another key: the backup is encrypted to exactly that key and
restore accepts exactly that signer. Export the signer's public key to the restore host
(`gpg --export S | gpg --import`) and check the fingerprint by eye against the one you wrote down.

## Taking a backup

```bash
scripts/backup.sh -o /srv/classbeat-backups --keep 14 \
  --gpg-recipient "$R_FPR" --gpg-sign "$S_FPR"
```

`pg_dump --format=custom` runs in the `db` container as the superuser over the local socket (no
password on a command line). The superuser is required: every owned table has forced row level
security, and a dump as `classbeat_owner` would silently skip rows. The dump is piped straight into
`gpg --encrypt --sign`: no plaintext dump touches the disk, and the output is
`classbeat-<UTC timestamp>.dump.gpg`, mode 600. The script **refuses to write anything that is not
encrypted and signed**: no flags, a recipient without a signer, or a short fingerprint is an error.
The encrypted archive is not read back by the script (that needs the private key R); the drill and a
monthly restore of a real backup are its test. `--keep N` (a positive whole number, checked before
anything is written or deleted) deletes older files named exactly
`classbeat-<UTC timestamp>.dump[.gpg]` (and their `.sig`) in the directory, never the backup it just
made. Stack selection is the usual compose one (`COMPOSE_PROJECT_NAME`, `.env`, `COMPOSE_FILE`).

### The unencrypted option (accepted risk)

```bash
scripts/backup.sh -o /srv/classbeat-backups --no-encrypt --sign-key /root/keys/backup-sign.key
```

`--no-encrypt` is the explicit way to get a plaintext dump. It prints a warning, and it still needs
a signing key: a PEM private key (`openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256`,
public half with `openssl pkey -pubout`) kept **outside the backup directory** (the script refuses a
key inside it). The signature is `classbeat-<timestamp>.dump.sig`. **This is an accepted risk, not a
recommendation:** the dump holds password hashes, hashed session tokens, class content, email
addresses and the ciphertext of every provider token. Use it only where the backup directory is
itself on encrypted storage that the attacker you worry about cannot read, and say so to the owner.
It was chosen as an option rather than removed so a restore is never blocked by a lost gpg key.
The owner's sign-off on using it in production is still to be recorded in `docs/status.md`.

**Recommended schedule and retention** (to be installed on the VPS by the owner): daily at 03:00
local (`cron` or a systemd timer calling the script), keep 14 dailies, plus copy the newest backup
weekly to storage off the host and keep 8 of those. Before every migration-bearing deploy take one
by hand. The restore drill below runs after each change to the scripts, the schema tooling or the
Postgres image, and the owner should run it by hand against a real backup (see below) monthly.

## Restoring into a fresh stack

```bash
export COMPOSE_PROJECT_NAME=classbeat-restore     # a NEW name: its own volume and ports
# put the secrets this stack needs in secrets/, KEK files included (see above)
scripts/restore.sh --expect-signer "$S_FPR" /srv/classbeat-backups/classbeat-20261031T030000Z.dump.gpg
# check the data, then:
docker compose up -d --wait web worker
```

(An unencrypted archive: `scripts/restore.sh --verify-key backup-sign.pub classbeat-….dump`, with
the public key kept outside the backup directory. `--verify-only` authenticates and validates and
stops, which is the cheap way to test a backup.)

What `restore.sh` does, in this order, and what it refuses:

1. **Authenticates the archive before anything runs as the database superuser.** It copies the
   archive to a mode 700 temporary directory and works on the copy, so the bytes that were verified
   are the bytes restored.
   - `.dump.gpg`: gpg must decrypt it cleanly, and it must carry a valid signature by the key whose
     primary fingerprint is `--expect-signer`, and by no other key. Unsigned, signed by someone
     else (an attacker can encrypt to your public key R), bad or expired signature, unknown signer,
     a changed byte: refused.
   - `.dump`: the detached `.sig` must verify with the public key given by `--verify-key`, which
     must not sit in the backup directory. **A `.sha256` next to the archive is never accepted**:
     whoever can replace the archive can replace the checksum.
2. **Validates the archive** (`scripts/validate-backup.sh`, below).
3. **Refuses the default project `classbeat` and a project whose volume `<project>_db-data` already
   exists**, before it starts anything; then starts `db` and only then **refuses a database that
   already holds tables** (it has started that project's `db`, but wrote nothing).
4. Runs `migrate` and `db-functions` (the schema, plus the `classbeat_queue` and `classbeat_maint`
   roles the dump refers to), runs `pg_restore --clean --if-exists --single-transaction
   --exit-on-error`, then `migrate` (a backup from an older release is upgraded to the image's head;
   a backup newer than the image fails loudly) and `db-functions` again. If anything fails nothing is
   restored: `docker compose down -v` and start over.

Web and worker refuse to start when `connection_kek` is not the key whose check value is in the
restored `connection_keks` (a wrong key under the same id), so a wrong key can never silently run.

To restore into the live project, stop the stack, take a backup of the broken state first, and
`docker compose down -v` it yourself; the scripts never delete a volume.

### What the validator allows

`scripts/validate-backup.sh` is **defence in depth, not the control**: the signature is. It runs
`pg_restore` in a throwaway container (`--network none`, no volume) and refuses an archive that is
correctly signed but could do more than classbeat's schema does as the superuser:

- Table of contents: custom format, database `classbeat`; entry types only TABLE, TABLE DATA,
  SEQUENCE (and SET, OWNED BY), CONSTRAINT, FK CONSTRAINT, INDEX, TRIGGER, POLICY, ROW SECURITY,
  FUNCTION, TYPE, DEFAULT, ACL, DEFAULT ACL, COMMENT, SCHEMA `public`; schema `public` (or none);
  owner `classbeat_owner`, `classbeat_queue`, `classbeat_maint` or `classbeat_app`. So no extension,
  event trigger, language, foreign data wrapper, publication, tablespace, cast, operator, other
  schema, or object owned by the superuser.
- The SQL `pg_restore` would send (with the options `restore.sh` uses), outside COPY data: no psql
  meta command (only the `\restrict` and `\unrestrict` brackets postgres 17.11 writes), no `ALTER
  SYSTEM`, role, database, tablespace, extension, event trigger, language, server or publication DDL,
  no `DROP` except the forms `--clean` writes, no `DO`, `SET ROLE` or `SESSION AUTHORIZATION`, no role
  grant, no file or program access (`COPY ... PROGRAM`, `pg_read_file`, `lo_import`, `dblink`), only
  `LANGUAGE plpgsql` and `sql`, only the two `SELECT` forms `pg_dump` writes, `COPY` only as
  `COPY public.<table> (...) FROM stdin;`.
- **Limits:** it is a denylist plus strict forms, so it cannot prove an archive harmless; COPY data
  is not inspected (it is data, and RLS and constraints apply as the schema defines); a function body
  can say anything that is not on the list. It reads the SQL line by line (not statement by statement),
  so a keyword split across lines, or a statement built at run time with `EXECUTE` inside a function
  body, is not seen; the signature is the control. A migration that adds an object type (a view, an enum) must
  add it to the validator in the same PR, or the drill and every restore of a newer backup fail.

## The drill

```bash
scripts/backup-restore-drill.sh [NAME]     # about 5 minutes with a cold image build
```

It needs `docker compose`, `gpg` and `openssl`, creates its own throwaway keys in a keyring of its
own, and creates `classbeat-drill-NAME-src` and `-dst` (own volumes, random free ports on
127.0.0.1; it touches no other project and removes both, their images, the keyring and its temp
files at the end, also on failure):

1. Source stack up; two users with passwords and an encrypted MOWL connection each; one sign-in.
2. A fingerprint of the database: row counts of every table, table owners, ACLs, RLS flags and
   the full policy definitions (command, permissive or restrictive, roles, USING and WITH CHECK),
   function owners, digests and ACLs, the `classbeat%` roles' attributes, migration head.
3. `scripts/backup.sh` (encrypted and signed); the decrypted and decoded dump (`pg_restore -f -`: the
   archive compresses its data, so the raw bytes cannot be searched) must not contain the seeded
   token or password as text or as hex (a bytea column such as `connections.secret` is printed as
   hex), and that check is first shown to find a token planted in a text column and in a bytea
   column. A token stored in a bytea as UTF-16 or base64 is not detected by it; the protection there
   is the decrypt and wrong-key probes. `--keep` is exercised (bad values refused with nothing
   deleted, a valid one prunes old backup files only).
4. **Authenticity** (all with `--verify-only`, nothing starts), each refused for the expected reason:
   - backup with no flags, with a recipient only, with a short fingerprint, `--no-encrypt` without a
     key or with the key inside the backup directory;
   - the unencrypted path works with the explicit flag and a signing key, and a plain archive is
     refused without `--verify-key` or with the key beside it;
   - an encrypted archive is refused with no, a different, or a short expected signer;
   - **swapped archives**: a valid dump signed by another gpg key; encrypted to R but unsigned; a
     plain dump with the attacker's signature; a plain dump with the real signature and a recomputed
     `.sha256`; a plain dump with only a `.sha256`;
   - **tampered archives**: one changed byte in the encrypted archive, in the plain archive, and in
     the signature;
   - a refused restore into the destination project creates no container and no volume;
   - signed archives that hold an event trigger, an extension, a superuser-owned function, a table in
     another schema, `ALTER SYSTEM` in a function body, or a C-language function are refused by the
     validator (built in a scratch Postgres container with no network).
5. The source stack and volume are destroyed; `scripts/restore.sh --expect-signer` restores the
   encrypted archive into the fresh `-dst` project; the fingerprint must be identical.
6. Web and worker start (the KEK check passes); migrations are at head; the right key decrypts both
   connections to the seeded tokens; with no user a session sees 0 connection rows and each user
   sees only their own; both users sign in over HTTP and a wrong password gets 401; writes that
   must be refused by RLS (moving a row to another owner, inserting a row for another owner) are
   refused.
7. Wrong keys: a different key under the same id is refused at startup (`KekError`); a different key
   under another id starts but gets `key_unavailable` for the old rows; the same wrong key used
   without the startup check fails authentication (`decrypt_failed`). These probes call the same
   functions web and worker call at startup (`load_keyring`) rather than restarting web; a wrong
   `connection_kek` file under the right id was also checked by hand to make web refuse to start.

CI runs it in the `e2e` job (on any change to the stack, the scripts or `docker/`), as a step before
Playwright. It adds about five minutes to a job that already runs for many; the second image build
is a layer-cache hit.

**Rollback is possible:** an older backup that is genuine and correctly signed is always accepted, so
whoever can plant an old signed archive in the backup directory can make a restore roll the data back.
The signature proves who made an archive, not that it is the newest: check the timestamp in the file
name (and that it is the one you mean) before restoring.

**What the drill does not prove:** that your real backup restores (it uses a seeded database; run
`scripts/restore.sh` on a copy of a real one at least monthly, into a throwaway project, and
`down -v` it afterwards); that the keys you generated are kept apart (private R off the VPS) and
that the fingerprint you pin is the one you wrote down; restoring a dump from an older release is
supported by the script (migrate runs after the restore) but not exercised by the drill; the VPS,
its disk, the schedule and the off-host copy do not exist yet. What the validator refuses is a
denylist (above), not a proof.
