#!/usr/bin/env bash
# The backup and restore drill (M6, issue #338; docs/runbooks/backup-restore.md).
#
#   scripts/backup-restore-drill.sh [NAME]
#
# NAME (default drill-<pid>) makes the two throwaway compose projects classbeat-drill-<NAME>-src
# and classbeat-drill-<NAME>-dst, each with its own volume and free ports on 127.0.0.1. It never
# touches any other project. Steps:
#   1. start the source stack, seed two users with encrypted connections, sign in once;
#   2. fingerprint the data (row counts per table, owners, ACLs, RLS flags and policies, roles);
#   3. scripts/backup.sh, then destroy the source stack and its volume;
#   4. scripts/restore.sh into the fresh destination project;
#   5. before the app starts: the fingerprint equals the source's;
#   6. start web and worker (the startup key check passes): the migrations are at head, the right
#      key decrypts both connections, RLS isolates users, sign-in works and a wrong password does
#      not, and a wrong key fails loudly (refused at startup under the same id, key_unavailable
#      under another id, decrypt_failed when used unchecked);
#   7. remove both projects, their images and the temporary files (also on failure).
# Uses the repository's secrets/ and .env like any stack; needs `docker compose` and `openssl`.
set -euo pipefail

cd "$(dirname "$0")/.."
name="${1:-drill-$$}"
[[ "$name" =~ ^[a-z0-9][a-z0-9-]{0,20}$ ]] || { echo "NAME must be lower-case letters, digits, dashes" >&2; exit 2; }
src="classbeat-drill-$name-src"
dst="classbeat-drill-$name-dst"
[ -s secrets/postgres_password ] || { echo "run scripts/worktree.sh init first" >&2; exit 2; }
# Never inherit a file list, profile or project from the caller's shell.
unset COMPOSE_FILE COMPOSE_PROFILES COMPOSE_PATH_SEPARATOR
export MSYS_NO_PATHCONV=1

work="$(mktemp -d)"
step() { printf '\n== %s\n' "$*"; }
fail() { echo "DRILL FAILED: $*" >&2; exit 1; }

free_port() {
  local p
  for _ in $(seq 1 50); do
    p=$((50000 + RANDOM % 10000))
    if ! (exec 3<> "/dev/tcp/127.0.0.1/$p") 2> /dev/null; then echo "$p"; return; fi
  done
  fail "no free port"
}
web_port="$(free_port)"; db_port="$(free_port)"; mail_port="$(free_port)"

use() { # use <project>: every docker compose call after it targets that project
  export COMPOSE_PROJECT_NAME="$1" WEB_PORT="$web_port" DB_PORT="$db_port" MAILPIT_PORT="$mail_port"
}

cleanup() {
  local code=$?
  set +e
  for p in "$src" "$dst"; do
    use "$p"
    if [ "$code" != 0 ]; then
      docker compose logs --no-color --tail 30 web worker 2>&1 | sed 's/^/  [log] /' >&2
    fi
    docker compose --profile '*' down -v --remove-orphans > /dev/null 2>&1
    docker image rm -f "$p:local" > /dev/null 2>&1
  done
  rm -rf "$work"
  if [ "$code" = 0 ]; then echo "drill: cleaned up $src and $dst"; else echo "drill: FAILED (cleaned up)" >&2; fi
  exit "$code"
}
trap cleanup EXIT

DRILL_PASSWORD="drill-$(openssl rand -hex 12)"
DRILL_TOKEN="drilltoken-$(openssl rand -hex 8)"
export DRILL_PASSWORD DRILL_TOKEN
users=(alice@drill.test bob@drill.test)

probe() { # probe <command> [args]: run scripts/drill/probe.py in the web container
  docker compose exec -T -e DRILL_PASSWORD -e DRILL_TOKEN web python - "$@" < scripts/drill/probe.py
}
sql() { docker compose exec -T db psql -U postgres -d classbeat -X -At -v ON_ERROR_STOP=1 "$@"; }

fingerprint() {
  echo "# row counts"
  local counts
  counts="$(sql -c "SELECT string_agg(format('SELECT %L AS t, count(*) AS n FROM public.%I', tablename, tablename),
                      ' UNION ALL ' ORDER BY tablename) FROM pg_tables WHERE schemaname = 'public'")"
  sql -c "$counts" | sort
  echo "# tables: owner, rls, forced rls, acl, policies"
  sql -c "SELECT c.relname, pg_get_userbyid(c.relowner), c.relrowsecurity, c.relforcerowsecurity,
                 coalesce(c.relacl, acldefault('r', c.relowner))::text,  -- a NULL ACL means the owner-only default
                 coalesce((SELECT string_agg(polname || ':' || pg_get_expr(polqual, polrelid), ',' ORDER BY polname)
                             FROM pg_policy p WHERE p.polrelid = c.oid), '')
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
           WHERE n.nspname = 'public' AND c.relkind = 'r' ORDER BY 1"
  echo "# functions: owner, definition digest, acl"
  sql -c "SELECT p.proname, pg_get_userbyid(p.proowner), md5(pg_get_functiondef(p.oid)), coalesce(p.proacl, acldefault('f', p.proowner))::text
            FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
           WHERE n.nspname = 'public' ORDER BY 1"
  echo "# roles"
  sql -c "SELECT rolname, rolsuper, rolbypassrls, rolcanlogin FROM pg_roles WHERE rolname LIKE 'classbeat%' ORDER BY 1"
  echo "# migration head"
  sql -c "SELECT version_num FROM alembic_version"
}

step "1. source stack $src (web :$web_port)"
use "$src"
docker compose build web
docker compose up -d --no-build --wait db web
probe seed "${users[@]}"
probe signin "${users[0]}"
fingerprint > "$work/before.txt"
users_n="$(grep -m1 '^users|' "$work/before.txt" | cut -d'|' -f2)"
conns_n="$(grep -m1 '^connections|' "$work/before.txt" | cut -d'|' -f2)"
[ "$users_n" = 2 ] && [ "$conns_n" = 2 ] || fail "seed: users=$users_n connections=$conns_n"
grep -q '|t|t|' "$work/before.txt" || fail "the source has no table with forced row level security"
echo "fingerprint of the source: $(grep -c . "$work/before.txt") lines, users=$users_n connections=$conns_n"

step "2. backup, then destroy the source"
dump="$(scripts/backup.sh -o "$work/backups" 2> /dev/null)"
[ -s "$dump" ] && [ -s "$dump.sha256" ] || fail "no backup file"
echo "backup: $(basename "$dump") ($(wc -c < "$dump") bytes)"
if grep -aqE "$DRILL_TOKEN|$DRILL_PASSWORD" "$dump"; then fail "plaintext token or password in the backup"; fi
echo "no plaintext token or password in the dump"
# The restore image: the source's, retagged for the destination project (no second build).
docker tag "$src:local" "$dst:local"
docker compose --profile '*' down -v --remove-orphans > /dev/null
echo "source stack and volume removed"

step "3. restore into the fresh project $dst"
use "$dst"
[ -z "$(docker volume ls -q --filter "name=^${dst}_db-data$")" ] || fail "the destination volume exists already"
scripts/restore.sh "$dump"
fingerprint > "$work/after.txt"
diff "$work/before.txt" "$work/after.txt" || fail "the restored database differs from the source (diff above)"
echo "row counts, owners, ACLs, RLS flags, policies, functions, roles and migration head are identical"

step "4. start the restored app"
docker compose up -d --no-build --wait web worker
docker compose run --rm --no-deps migrate alembic current 2>&1 | grep -q '(head)' || fail "migrations are not at head"
echo "migrations at head"
probe decrypt "${users[@]}"
probe rls "${users[@]}"
probe signin "${users[1]}"
probe badsignin "${users[1]}"

step "5. wrong keys fail loudly"
probe wrongkey-same-id "${users[@]}"
probe wrongkey-other-id "${users[@]}"
probe wrongkey-unchecked "${users[@]}"

step "DRILL PASSED"
