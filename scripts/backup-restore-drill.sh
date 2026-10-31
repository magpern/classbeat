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
#   3. scripts/backup.sh (the decoded dump must hold no plaintext token or password; that check is
#      first shown to find a planted token), then destroy the source stack and its volume;
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
trap 'rm -rf "$work"' EXIT # replaced by the full cleanup once the projects are known
step() { printf '\n== %s\n' "$*"; }
fail() { echo "DRILL FAILED: $*" >&2; exit 1; }

# A port is free when nothing answers on it and (where python exists) it can be bound: on Windows
# a port inside an excluded range refuses the bind although nothing listens.
py="$(command -v python3 || command -v python || true)"
port_ok() {
  if (exec 3<> "/dev/tcp/127.0.0.1/$1") 2> /dev/null; then return 1; fi
  [ -z "$py" ] || "$py" -c 'import socket,sys; s=socket.socket(); s.bind(("127.0.0.1", int(sys.argv[1])))' "$1" 2> /dev/null
}
free_port() { # free_port <ports already taken...>
  local p
  for _ in $(seq 1 100); do
    p=$((50000 + RANDOM % 10000))
    case " $* " in *" $p "*) continue ;; esac
    if port_ok "$p"; then echo "$p"; return; fi
  done
  fail "no free port"
}
web_port="$(free_port)"
db_port="$(free_port "$web_port")"
mail_port="$(free_port "$web_port" "$db_port")"

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
                 coalesce((SELECT string_agg(concat_ws(':', polname, polcmd, polpermissive, polroles::regrole[]::text,
                                'using=' || coalesce(pg_get_expr(polqual, polrelid), '<none>'),
                                'check=' || coalesce(pg_get_expr(polwithcheck, polrelid), '<none>')), ',' ORDER BY polname)
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
# pg_dump -Fc compresses table data, so the archive is decoded (pg_restore -f -) before searching.
# A bytea column is printed as hex, so each value is searched as text and as lower and upper hex.
hex_of() { printf '%s' "$1" | od -An -tx1 | tr -d ' 
'; }
patterns() { # patterns <value>: the text and both hex spellings, as an extended regex
  local h; h="$(hex_of "$1")"
  printf '%s|%s|%s' "$1" "$h" "$(printf '%s' "$h" | tr 'a-f' 'A-F')"
}
dump_has() { docker compose exec -T db pg_restore -f - < "$1" | grep -aE "$2" > /dev/null; }  # no -q: read it all, no SIGPIPE
dump_has_secret() { dump_has "$1" "$(patterns "$DRILL_TOKEN")|$(patterns "$DRILL_PASSWORD")"; }
# Self-test of that check: plant the token in a table, back up, the check must FIND it.
sql -c "CREATE TABLE drill_plant_text AS SELECT 'text-$DRILL_TOKEN'::text AS v" > /dev/null
sql -c "CREATE TABLE drill_plant_bytea AS SELECT convert_to('bytea-$DRILL_TOKEN', 'UTF8') AS v" > /dev/null
planted="$(scripts/backup.sh -o "$work/planted" 2> /dev/null)"
dump_has "$planted" "text-$DRILL_TOKEN" || fail "self-test: a planted text value was not found"
dump_has "$planted" "$(patterns "bytea-$DRILL_TOKEN" | cut -d'|' -f2,3)" || fail "self-test: a planted bytea value was not found"
dump_has_secret "$planted" || fail "self-test: the combined check missed the planted values"
sql -c "DROP TABLE drill_plant_text, drill_plant_bytea" > /dev/null
echo "self-test: the check finds a token planted in a text column and in a bytea column"
dump="$(scripts/backup.sh -o "$work/backups" 2> /dev/null)"
[ -s "$dump" ] && [ -s "$dump.sha256" ] || fail "no backup file"
echo "backup: $(basename "$dump") ($(wc -c < "$dump") bytes)"
if dump_has_secret "$dump"; then fail "plaintext token or password in the backup"; fi
echo "no plaintext token or password in the decoded dump"
# --keep: invalid values are refused before anything is deleted; a valid one prunes old backups
# only and never the new one.
mkdir -p "$work/keep"
for d in 20200101T000000Z 20200102T000000Z 20200103T000000Z; do touch "$work/keep/classbeat-$d.dump"; done
touch "$work/keep/classbeat-notes.dump" "$work/keep/classbeat-20200101T000000Z.dump.bak" "$work/keep/other.txt" # never pruned
for bad in 0 00 000 abc -1 1.5 ""; do
  if scripts/backup.sh -o "$work/keep" --keep "$bad" > /dev/null 2>&1; then fail "--keep '$bad' was accepted"; fi
done
[ "$(find "$work/keep" -type f | wc -l)" = 6 ] || fail "a refused --keep changed the backup directory"
kept="$(scripts/backup.sh -o "$work/keep" --keep 2 2> /dev/null)"
[ -s "$kept" ] && [ "$(find "$work/keep" -type f -name 'classbeat-2*Z.dump' | wc -l)" = 2 ] \
  && [ -e "$work/keep/classbeat-20200103T000000Z.dump" ] || fail "--keep 2 did not keep exactly the newest two"
for decoy in classbeat-notes.dump classbeat-20200101T000000Z.dump.bak other.txt; do
  [ -e "$work/keep/$decoy" ] || fail "--keep deleted $decoy, which is not a backup file"
done
echo "backup.sh --keep: bad values refused untouched; --keep 2 keeps the new backup and the newest old one; files that are not backups are left alone"
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
