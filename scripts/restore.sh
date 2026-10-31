#!/usr/bin/env bash
# Restore a backup into a FRESH compose stack (M6, docs/runbooks/backup-restore.md).
#
#   COMPOSE_PROJECT_NAME=classbeat-restore scripts/restore.sh --expect-signer FPR BACKUP.dump.gpg
#   COMPOSE_PROJECT_NAME=classbeat-restore scripts/restore.sh --verify-key PUB.pem BACKUP.dump
#   ... --verify-only  authenticate and validate the archive, then stop (nothing is started)
#
# The archive is authenticated BEFORE anything runs as the database superuser (SR-06, #355):
#   .dump.gpg  decrypted and verified by gpg; it must carry a valid signature by exactly the key
#              with the fingerprint given by --expect-signer (or RESTORE_EXPECT_SIGNER), the
#              primary key's, and by no other. The signer's public key must be in the keyring.
#   .dump      the detached signature <name>.dump.sig must verify with the public key in
#              --verify-key (or RESTORE_VERIFY_KEY), a PEM file kept OUTSIDE the backup
#              directory. A .sha256 file next to the archive is never accepted.
# Both work on a private copy in a mode 700 temporary directory, so the bytes that were verified
# are the bytes that are restored. Then scripts/validate-backup.sh checks the archive holds only
# classbeat's own schema (defence in depth; the signature is the control).
#
# Use a new project name (its own db volume). The script refuses the default project `classbeat`,
# a project whose volume exists, and a database that already holds tables; after a failed
# restore, remove the project (`docker compose down -v`) and start again. The secrets/ directory
# is the operator's own: the roles' passwords and the KEK files come from there, never from the
# backup.
#
# Steps: authenticate; validate; project and volume guards; start `db` (its init creates
# classbeat_owner and classbeat_app); check it is empty; run `migrate` and `db-functions` (the
# schema, the queue and admin roles and functions the dump refers to); pg_restore with --clean in
# one transaction; `migrate` again (an older backup is upgraded to this image's head) and
# `db-functions` again. It does not start web or worker: do that once you have checked the data
# (`docker compose up -d --wait web worker`). Web and worker refuse to start when connection_kek
# is not the key whose check value is in the restored database.
set -euo pipefail

die() { echo "restore.sh: $*" >&2; exit 1; }
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
expect="${RESTORE_EXPECT_SIGNER:-}"
vkey="${RESTORE_VERIFY_KEY:-}"
verify_only=0
file=""
while [ $# -gt 0 ]; do
  case "$1" in
    --expect-signer) [ $# -ge 2 ] || die "--expect-signer needs a fingerprint"; expect="$2"; shift 2 ;;
    --verify-key) [ $# -ge 2 ] || die "--verify-key needs a file"; vkey="$2"; shift 2 ;;
    --verify-only) verify_only=1; shift ;;
    -h|--help) sed -n '2,31p' "$0"; exit 0 ;;
    -*) die "unknown argument: $1" ;;
    *) [ -z "$file" ] || die "one archive only"; file="$1"; shift ;;
  esac
done
[ -n "$file" ] || { sed -n '2,31p' "$0" >&2; exit 2; }
[ -f "$file" ] || die "no such file: $file"

umask 077
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
dump="$tmp/archive.dump"

# --- 1. authenticate ------------------------------------------------------------------------
if [[ "$file" == *.dump.gpg ]]; then
  command -v gpg > /dev/null || die "gpg is not installed"
  [[ "$expect" =~ ^[0-9A-Fa-f]{40}$ ]] \
    || die "an encrypted archive needs --expect-signer with the signer's full 40-digit fingerprint"
  expect="${expect^^}"
  cp -- "$file" "$tmp/copy.gpg"
  gpg --batch --yes --status-file "$tmp/status" --decrypt --output "$dump" "$tmp/copy.gpg" 2> /dev/null \
    || die "cannot decrypt or verify $file; refusing"
  grep -q '^\[GNUPG:\] DECRYPTION_OKAY' "$tmp/status" || die "gpg did not report a clean decryption; refusing"
  if grep -qE '^\[GNUPG:\] (BADSIG|ERRSIG|EXPSIG|EXPKEYSIG|REVKEYSIG|NO_PUBKEY|BADMDC|DECRYPTION_FAILED|NODATA)' "$tmp/status"; then
    die "the signature is bad, expired, revoked or from an unknown key; refusing"
  fi
  # Every valid signature must be the expected key's (its primary fingerprint), and there must be one.
  sigs="$(grep -c '^\[GNUPG:\] VALIDSIG ' "$tmp/status" || true)"
  [ "$sigs" -ge 1 ] || die "the archive is not signed; refusing"
  other="$(awk -v e="$expect" '$1 == "[GNUPG:]" && $2 == "VALIDSIG" { if (toupper($3) != e && toupper($NF) != e) n++ } END { print n + 0 }' "$tmp/status")"
  [ "$other" = 0 ] || die "the archive carries a signature by a key other than $expect; refusing"
  echo "restore.sh: decrypted; signature verified for $expect"
elif [[ "$file" == *.dump ]]; then
  command -v openssl > /dev/null || die "openssl is not installed"
  [ -n "$vkey" ] || die "a plain archive needs --verify-key (the public key; a checksum file is not accepted)"
  [ -f "$vkey" ] || die "no such verification key file: $vkey"
  [ -f "$file.sig" ] || die "no signature $file.sig; refusing"
  arch_real="$(cd "$(dirname "$file")" && pwd -P)"
  key_real="$(cd "$(dirname "$vkey")" && pwd -P)"
  [ "$key_real" != "$arch_real" ] || die "the verification key must be kept outside the backup directory"
  cp -- "$file" "$tmp/copy.dump"
  cp -- "$file.sig" "$tmp/copy.sig"
  out="$(openssl dgst -sha256 -verify "$vkey" -signature "$tmp/copy.sig" "$tmp/copy.dump" 2>&1)" \
    && [ "$out" = "Verified OK" ] || die "the signature does not verify with $vkey; refusing"
  mv "$tmp/copy.dump" "$dump"
  echo "restore.sh: signature verified with $vkey"
else
  die "an archive is classbeat-<timestamp>.dump or .dump.gpg"
fi

# --- 2. validate ----------------------------------------------------------------------------
"$here/validate-backup.sh" "$dump" || die "the archive is authentic but not a classbeat backup; refusing"
if [ "$verify_only" = 1 ]; then
  echo "restore.sh: authenticated and validated; --verify-only, nothing was started"
  exit 0
fi

# --- 3. guards, then restore ----------------------------------------------------------------
project="$(docker compose config --format json | sed -n 's/^ *"name": *"\([^"]*\)".*/\1/p' | head -n 1)"
[ -n "$project" ] || die "cannot tell the compose project name"
[ "$project" != classbeat ] \
  || die "refusing the default project 'classbeat' (the live stack); set COMPOSE_PROJECT_NAME to a new name"
[ -z "$(docker volume ls -q --filter "name=^${project}_db-data$")" ] \
  || die "the volume ${project}_db-data exists: restore only into a fresh project (docker compose down -v first, or pick a new name)"
echo "restore.sh: restoring into compose project '$project'"

docker compose up -d --wait db
psql_db() { docker compose exec -T db psql -U postgres -d classbeat -X -At -v ON_ERROR_STOP=1 "$@"; }
tables="$(psql_db -c "SELECT count(*) FROM pg_tables WHERE schemaname = 'public'")"
[ "$tables" = 0 ] \
  || die "the database already has $tables tables: restore only into a fresh project (docker compose down -v first)"

docker compose up -d db-functions   # starts after migrate has completed
for svc in migrate db-functions; do
  cid="$(docker compose ps -aq "$svc")"
  [ -n "$cid" ] && [ "$(docker wait "$cid")" = 0 ] || die "$svc failed"
done
docker compose exec -T db pg_restore -U postgres -d classbeat \
  --clean --if-exists --single-transaction --exit-on-error --no-password < "$dump" \
  || die "pg_restore failed; nothing was restored (single transaction)"
docker compose run --rm --no-deps migrate
docker compose run --rm --no-deps db-functions
echo "restore.sh: restored. Start the app with: docker compose up -d --wait web worker"
