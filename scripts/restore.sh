#!/usr/bin/env bash
# Restore a backup into a FRESH compose stack (M6, docs/runbooks/backup-restore.md).
#
#   COMPOSE_PROJECT_NAME=classbeat-restore scripts/restore.sh BACKUP.dump[.gpg]
#
# Use a new project name (its own db volume). The script refuses a database that already holds
# tables, so it cannot overwrite a live stack by mistake; after a failed restore, remove the
# project (`docker compose down -v`) and start again. The secrets/ directory of the new project
# is the operator's own: the roles' passwords and the KEK files come from there, never from the
# backup.
#
# Steps: verify the .sha256 next to the backup; start `db` (its init creates classbeat_owner and
# classbeat_app); check it is empty; run `migrate` and `db-functions` (create the schema, the
# queue and admin roles and functions the dump refers to); pg_restore with --clean in one
# transaction; run `migrate` again (a backup from an older release is upgraded to this image's
# head) and `db-functions` again (re-applies the roles, grants and queue functions).
# It does not start web or worker: do that once you have checked the data
# (`docker compose up -d --wait web worker`). Web and worker refuse to start when connection_kek
# is not the key whose check value is in the restored database.
set -euo pipefail

die() { echo "restore.sh: $*" >&2; exit 1; }
[ $# -eq 1 ] && [ "$1" != -h ] && [ "$1" != --help ] || { sed -n '2,19p' "$0"; exit 2; }
file="$1"
[ -f "$file" ] || die "no such file: $file"

if [ -f "$file.sha256" ]; then
  (cd "$(dirname "$file")" && sha256sum --check --status "$(basename "$file").sha256") \
    || die "the checksum does not match $file.sha256"
  echo "restore.sh: checksum ok"
else
  echo "restore.sh: warning: no $file.sha256, integrity not checked" >&2
fi

dump="$file"
tmp=""
if [[ "$file" == *.gpg ]]; then
  command -v gpg > /dev/null || die "gpg is not installed"
  umask 077
  tmp="$(mktemp)"
  trap 'rm -f "$tmp"' EXIT
  gpg --batch --yes --decrypt --output "$tmp" "$file" || die "cannot decrypt $file"
  dump="$tmp"
fi

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
