#!/usr/bin/env bash
# Back up the classbeat database of a compose stack (M6, docs/runbooks/backup-restore.md).
#
#   scripts/backup.sh [-o DIR] [--keep N] [--gpg-recipient KEY]
#
# Runs pg_dump (custom format) inside the stack's `db` container as the superuser over the local
# socket: the superuser is the only role that reads every table through forced row level security
# (a dump as classbeat_owner would silently skip rows). No password is used or printed.
#
# In the backup: the database `classbeat` (schema, data, grants, policies, functions), which holds
# password hashes, session token hashes and the connections' ciphertext. NOT in it: the secrets/
# directory, the KEK files, the roles' passwords (cluster roles are not dumped). Without the KEK
# file of the rows' key id, the connections in the backup stay unreadable by design.
#
# The file is written with mode 600 next to a .sha256. Treat it as sensitive: store it encrypted.
# --gpg-recipient KEY encrypts the dump to that public key (gpg) and writes <name>.dump.gpg.
# --keep N keeps the newest N backups in DIR and deletes older ones.
# The stack is picked like any compose command: COMPOSE_PROJECT_NAME / .env / COMPOSE_FILE.
set -euo pipefail

out=backups
keep=""
recipient=""
die() { echo "backup.sh: $*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    -o) [ $# -ge 2 ] || die "-o needs a directory"; out="$2"; shift 2 ;;
    --keep) [ $# -ge 2 ] || die "--keep needs a number"; keep="$2"; shift 2 ;;
    --gpg-recipient) [ $# -ge 2 ] || die "--gpg-recipient needs a key"; recipient="$2"; shift 2 ;;
    -h|--help) sed -n '2,19p' "$0"; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done
case "$keep" in ""|*[!0-9]*) [ -z "$keep" ] || die "--keep must be a number" ;; esac
[ "$keep" != 0 ] || die "--keep must be at least 1"
if [ -n "$recipient" ]; then command -v gpg > /dev/null || die "gpg is not installed"; fi

docker compose ps --status running --services 2> /dev/null | grep -qx db \
  || die "the db service of this compose project is not running"

umask 077
mkdir -p "$out"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
name="classbeat-$stamp.dump"
partial="$out/$name.partial"
trap 'rm -f "$partial" "$partial.gpg"' EXIT

docker compose exec -T db pg_dump -U postgres -d classbeat --format=custom > "$partial"
[ -s "$partial" ] || die "pg_dump produced an empty file"
# The archive must be readable by pg_restore and list its table of contents.
docker compose exec -T db pg_restore --list < "$partial" > /dev/null \
  || die "the dump cannot be read back by pg_restore"

final="$out/$name"
if [ -n "$recipient" ]; then
  gpg --batch --yes --trust-model always --encrypt --recipient "$recipient" \
    --output "$partial.gpg" "$partial"
  rm -f "$partial"
  final="$final.gpg"
  mv "$partial.gpg" "$final"
else
  echo "backup.sh: not encrypted; encrypt it before it leaves this host (--gpg-recipient)" >&2
  mv "$partial" "$final"
fi
chmod 600 "$final"
(cd "$out" && sha256sum "$(basename "$final")" > "$(basename "$final").sha256")
echo "$final"

if [ -n "$keep" ]; then
  shopt -s nullglob
  # Names start with a UTC timestamp, so a reverse sort is newest first.
  mapfile -t all < <(printf '%s\n' "$out"/classbeat-*.dump "$out"/classbeat-*.dump.gpg | sort -r)
  if [ "${#all[@]}" -gt "$keep" ]; then
    for old in "${all[@]:$keep}"; do
      rm -f "$old" "$old.sha256"
      echo "backup.sh: pruned $old" >&2
    done
  fi
fi
