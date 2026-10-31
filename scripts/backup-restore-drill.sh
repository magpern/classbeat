#!/usr/bin/env bash
# The backup and restore drill (M6, issues #338 and #355; docs/runbooks/backup-restore.md).
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
#      Also (SR-06): backup.sh refuses to write an unencrypted backup; restore.sh refuses a
#      swapped archive (another signer, no signature, a recomputed checksum), a tampered one, and
#      a signed archive with objects classbeat does not have; the --no-encrypt path works only
#      with its flag and a signing key. Throwaway gpg and openssl keys, in a keyring of its own.
#   4. scripts/restore.sh (--expect-signer) into the fresh destination project;
#   5. before the app starts: the fingerprint equals the source's;
#   6. start web and worker (the startup key check passes): the migrations are at head, the right
#      key decrypts both connections, RLS isolates users, sign-in works and a wrong password does
#      not, and a wrong key fails loudly (refused at startup under the same id, key_unavailable
#      under another id, decrypt_failed when used unchecked);
#   7. remove both projects, their images and the temporary files (also on failure).
# Uses the repository's secrets/ and .env like any stack; needs `docker compose`, `gpg` and `openssl`.
set -euo pipefail

cd "$(dirname "$0")/.."
name="${1:-drill-$$}"
[[ "$name" =~ ^[a-z0-9][a-z0-9-]{0,20}$ ]] || { echo "NAME must be lower-case letters, digits, dashes" >&2; exit 2; }
src="classbeat-drill-$name-src"
dst="classbeat-drill-$name-dst"
[ -s secrets/postgres_password ] || { echo "run scripts/worktree.sh init first" >&2; exit 2; }
# Never inherit a file list, profile or project from the caller's shell.
unset COMPOSE_FILE COMPOSE_PROFILES COMPOSE_PATH_SEPARATOR

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT # replaced by the full cleanup once the projects are known
step() { printf '\n== %s\n' "$*"; }
fail() { echo "DRILL FAILED: $*" >&2; exit 1; }

# Throwaway keys, all in a keyring of their own: R (backups are encrypted to it), S (the signer
# restore.sh must see), X (an attacker who knows R's public key), and an openssl pair for the
# --no-encrypt path plus an attacker's. Nothing here is a real key; none leaves $work.
export GNUPGHOME="$work/gnupg"
mkdir "$GNUPGHOME" "$work/keys"; chmod 700 "$GNUPGHOME" 2> /dev/null || true
for who in recipient signer attacker; do
  gpg --batch --pinentry-mode loopback --passphrase '' --quick-generate-key "drill-$who" default default never 2> /dev/null
done
fpr_of() { gpg --batch --with-colons --list-keys "drill-$1" | awk -F: '/^fpr:/ && !d { print $10; d = 1 }'; }
R="$(fpr_of recipient)"; S="$(fpr_of signer)"; X="$(fpr_of attacker)"
for k in sign attacker; do
  openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -out "$work/keys/$k.key" 2> /dev/null
  openssl pkey -in "$work/keys/$k.key" -pubout -out "$work/keys/$k.pub"
done
bk() { scripts/backup.sh "$@" --gpg-recipient "$R" --gpg-sign "$S"; }  # the encrypted default
rs() { scripts/restore.sh --expect-signer "$S" "$@"; }

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
  gpgconf --kill gpg-agent > /dev/null 2>&1
  [ -z "${scratch:-}" ] || docker rm -f "$scratch" > /dev/null 2>&1
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
decoded() { # the SQL of a backup, decrypted first when it is a .gpg
  if [[ "$1" == *.gpg ]]; then gpg --batch --decrypt "$1" 2> /dev/null; else cat "$1"; fi     | docker compose exec -T db pg_restore -f -
}
dump_has() { decoded "$1" | grep -aE "$2" > /dev/null; }  # no -q: read it all, no SIGPIPE
dump_has_secret() { dump_has "$1" "$(patterns "$DRILL_TOKEN")|$(patterns "$DRILL_PASSWORD")"; }
# Self-test of that check: plant the token in a table, back up, the check must FIND it.
sql -c "CREATE TABLE drill_plant_text AS SELECT 'text-$DRILL_TOKEN'::text AS v" > /dev/null
sql -c "CREATE TABLE drill_plant_bytea AS SELECT convert_to('bytea-$DRILL_TOKEN', 'UTF8') AS v" > /dev/null
planted="$(bk -o "$work/planted" 2> /dev/null)"
dump_has "$planted" "text-$DRILL_TOKEN" || fail "self-test: a planted text value was not found"
dump_has "$planted" "$(patterns "bytea-$DRILL_TOKEN" | cut -d'|' -f2,3)" || fail "self-test: a planted bytea value was not found"
dump_has_secret "$planted" || fail "self-test: the combined check missed the planted values"
sql -c "DROP TABLE drill_plant_text, drill_plant_bytea" > /dev/null
echo "self-test: the check finds a token planted in a text column and in a bytea column"
dump="$(bk -o "$work/backups" 2> /dev/null)"
[ -s "$dump" ] && [[ "$dump" == *.dump.gpg ]] || fail "no encrypted backup file"
[ ! -e "$dump.sha256" ] || fail "a .sha256 file is no longer written"
grep -aq "PGDMP" "$dump" && fail "the default backup holds a readable archive header" || true
echo "backup: $(basename "$dump") ($(wc -c < "$dump") bytes)"
if dump_has_secret "$dump"; then fail "plaintext token or password in the backup"; fi
echo "no plaintext token or password in the decoded dump"
# --keep: invalid values are refused before anything is deleted; a valid one prunes old backups
# only and never the new one.
mkdir -p "$work/keep"
for d in 20200101T000000Z 20200102T000000Z 20200103T000000Z; do touch "$work/keep/classbeat-$d.dump"; done
touch "$work/keep/classbeat-notes.dump" "$work/keep/classbeat-20200101T000000Z.dump.bak" "$work/keep/other.txt" # never pruned
for bad in 0 00 000 abc -1 1.5 ""; do
  if bk -o "$work/keep" --keep "$bad" > /dev/null 2>&1; then fail "--keep '$bad' was accepted"; fi
done
[ "$(find "$work/keep" -type f | wc -l)" = 6 ] || fail "a refused --keep changed the backup directory"
kept="$(bk -o "$work/keep" --keep 2 2> /dev/null)"
[ -s "$kept" ] && [ "$(find "$work/keep" -type f -regextype posix-extended -regex '.*/classbeat-[0-9]{8}T[0-9]{6}Z\.dump(\.gpg)?' | wc -l)" = 2 ] \
  && [ -e "$work/keep/classbeat-20200103T000000Z.dump" ] || fail "--keep 2 did not keep exactly the newest two"
for decoy in classbeat-notes.dump classbeat-20200101T000000Z.dump.bak other.txt; do
  [ -e "$work/keep/$decoy" ] || fail "--keep deleted $decoy, which is not a backup file"
done
echo "backup.sh --keep: bad values refused untouched; --keep 2 keeps the new backup and the newest old one; files that are not backups are left alone"

step "2b. authenticity: the archive is checked before anything runs as the superuser"
refused() { # refused <label> <reason regex> <command...>: the command must fail, for that reason
  local label="$1" why="$2"; shift 2
  if "$@" > "$work/out.txt" 2>&1; then fail "$label: was accepted"; fi
  grep -q "$why" "$work/out.txt" || { cat "$work/out.txt" >&2; fail "$label: refused, but not for the expected reason ($why)"; }
  echo "refused: $label"
}
# Encrypted by default: no flags, one flag, or a short fingerprint write nothing.
mkdir -p "$work/none"
refused "backup with no encryption options" "refusing to write an unencrypted backup" scripts/backup.sh -o "$work/none"
refused "backup with a recipient but no signer" "refusing to write an unencrypted backup" scripts/backup.sh -o "$work/none" --gpg-recipient "$R"
refused "backup with a short fingerprint" "full 40-digit" scripts/backup.sh -o "$work/none" --gpg-recipient "${R:0:16}" --gpg-sign "$S"
refused "--no-encrypt without a signing key" "still needs --sign-key" scripts/backup.sh -o "$work/none" --no-encrypt
key_inside() { mkdir -p "$work/in"; cp "$work/keys/sign.key" "$work/in/k.key"; scripts/backup.sh -o "$work/in" --no-encrypt --sign-key "$work/in/k.key"; }
refused "--no-encrypt with the signing key inside the backup directory" "outside the backup directory" key_inside
[ -z "$(find "$work/none" -type f)" ] || fail "a refused backup left a file"
# The explicit, signed, unencrypted path (accepted risk): works, and restore wants the public key.
plain="$(scripts/backup.sh -o "$work/plain" --no-encrypt --sign-key "$work/keys/sign.key" 2> /dev/null)"
[ -s "$plain" ] && [ -s "$plain.sig" ] && [[ "$plain" == *.dump ]] || fail "no signed plain backup"
scripts/restore.sh --verify-only --verify-key "$work/keys/sign.pub" "$plain" > /dev/null || fail "the signed plain backup was not accepted"
echo "ok: --no-encrypt backup, signed, verifies with the public key"
refused "plain archive without --verify-key" "needs --verify-key" scripts/restore.sh --verify-only "$plain"
pub_beside() { cp "$work/keys/sign.pub" "$work/plain/k.pub"; scripts/restore.sh --verify-only --verify-key "$work/plain/k.pub" "$plain"; }
refused "plain archive with the verification key beside it" "outside the backup directory" pub_beside
# The encrypted round trip itself is step 3. Here: the same archive, verify-only, wrong expectations.
scripts/restore.sh --verify-only --expect-signer "$S" "$dump" > /dev/null || fail "the encrypted backup was not accepted"
refused "encrypted archive with no --expect-signer" "needs --expect-signer" scripts/restore.sh --verify-only "$dump"
refused "encrypted archive, a different expected signer" "other than" scripts/restore.sh --verify-only --expect-signer "$X" "$dump"
refused "encrypted archive, a short expected signer" "needs --expect-signer" scripts/restore.sh --verify-only --expect-signer "${S:0:16}" "$dump"
# A swapped archive: a valid dump of this database, made by someone who knows only the PUBLIC keys.
mkdir -p "$work/attack"
docker compose exec -T db pg_dump -U postgres -d classbeat --format=custom > "$work/attack/their.dump"
gpg --batch --yes --trust-model always --encrypt --sign --local-user "$X" --recipient "$R" \
  --output "$work/attack/signed-by-x.dump.gpg" "$work/attack/their.dump"
gpg --batch --yes --trust-model always --encrypt --recipient "$R" \
  --output "$work/attack/unsigned.dump.gpg" "$work/attack/their.dump"
refused "swapped archive signed by another key" "other than" rs --verify-only "$work/attack/signed-by-x.dump.gpg"
refused "swapped archive, encrypted but not signed" "not signed" rs --verify-only "$work/attack/unsigned.dump.gpg"
openssl dgst -sha256 -sign "$work/keys/attacker.key" -out "$work/attack/their.dump.sig" "$work/attack/their.dump"
refused "swapped plain archive with the attacker's signature" "does not verify" \
  scripts/restore.sh --verify-only --verify-key "$work/keys/sign.pub" "$work/attack/their.dump"
cp "$plain.sig" "$work/attack/their.dump.sig"
recomputed_sha() {
  (cd "$work/attack" && sha256sum their.dump > their.dump.sha256)
  scripts/restore.sh --verify-only --verify-key "$work/keys/sign.pub" "$work/attack/their.dump"
}
refused "swapped plain archive with the real signature and a recomputed .sha256" "does not verify" recomputed_sha
rm -f "$work/attack/their.dump.sig"
refused "swapped plain archive with only a .sha256" "no signature" scripts/restore.sh --verify-only --verify-key "$work/keys/sign.pub" "$work/attack/their.dump"
# A tampered archive: one byte changed.
flip() { # flip <in> <out> <offset>
  cp "$1" "$2"
  local b; b="$(od -An -tu1 -j"$3" -N1 "$1" | tr -d ' ')"
  printf '%b' "\\0$(printf '%o' $(( b ^ 1 )))" | dd of="$2" bs=1 seek="$3" conv=notrunc 2> /dev/null
}
flip "$dump" "$work/attack/tampered.dump.gpg" "$(( $(wc -c < "$dump") / 2 ))"
refused "tampered encrypted archive" "cannot decrypt\|clean decryption\|bad, expired" rs --verify-only "$work/attack/tampered.dump.gpg"
flip "$plain" "$work/attack/tampered.dump" "$(( $(wc -c < "$plain") / 2 ))"
cp "$plain.sig" "$work/attack/tampered.dump.sig"
refused "tampered plain archive" "does not verify" scripts/restore.sh --verify-only --verify-key "$work/keys/sign.pub" "$work/attack/tampered.dump"
cp "$plain" "$work/attack/badsig.dump"
flip "$plain.sig" "$work/attack/badsig.dump.sig" 10
refused "plain archive with a tampered signature" "does not verify" scripts/restore.sh --verify-only --verify-key "$work/keys/sign.pub" "$work/attack/badsig.dump"
# A refused archive starts nothing: restoring the swapped one into the destination project does nothing.
use "$dst"
refused "restore of the swapped archive into $dst" "other than" rs "$work/attack/signed-by-x.dump.gpg"
[ -z "$(docker volume ls -q --filter "name=^${dst}_")" ] && [ -z "$(docker compose ps -aq)" ] || fail "a refused restore created a volume or a container"
echo "ok: a refused archive starts no container and creates no volume"
use "$src"

# Authentic but not a classbeat backup (signed with the real key): the validator refuses these.
step "2c. validation: signed archives with objects classbeat does not have"
pgimg="$(docker compose config --images | grep -m1 '^postgres:')"
scratch="$(docker run -d --rm --network none -e POSTGRES_HOST_AUTH_METHOD=trust "$pgimg")"
for _ in $(seq 1 60); do docker exec "$scratch" pg_isready -q -h 127.0.0.1 -U postgres && break; sleep 1; done
evil() { # evil <name> <expected text in the refusal> <sql run as the superuser in a fresh database classbeat>
  local name="$1" want="$2" statement="$3" file="$work/attack/evil-$1.dump"
  docker exec "$scratch" psql -q -h 127.0.0.1 -U postgres -d postgres -c "DROP DATABASE IF EXISTS classbeat" -c "CREATE DATABASE classbeat" > /dev/null
  docker exec "$scratch" psql -q -h 127.0.0.1 -U postgres -d classbeat -v ON_ERROR_STOP=1 -c "$statement" > /dev/null
  docker exec "$scratch" pg_dump -h 127.0.0.1 -U postgres -d classbeat --format=custom > "$file"
  openssl dgst -sha256 -sign "$work/keys/sign.key" -out "$file.sig" "$file"
  if scripts/restore.sh --verify-only --verify-key "$work/keys/sign.pub" "$file" > "$work/out.txt" 2>&1; then fail "evil archive $name was accepted"; fi
  grep -q "$want" "$work/out.txt" || { cat "$work/out.txt" >&2; fail "evil archive $name was refused for another reason (wanted: $want)"; }
  echo "refused: signed archive with $name"
}
docker exec "$scratch" psql -q -h 127.0.0.1 -U postgres -d postgres -c "CREATE ROLE classbeat_owner SUPERUSER" > /dev/null
evil "an event trigger" "entry type not allowed: EVENT" \
  'CREATE FUNCTION public.f() RETURNS event_trigger LANGUAGE plpgsql AS $$ BEGIN NULL; END $$; CREATE EVENT TRIGGER evt ON ddl_command_start EXECUTE FUNCTION public.f();'
evil "an extension" "entry type not allowed: EXTENSION" 'CREATE EXTENSION pgcrypto;'
evil "a function owned by the superuser" "owner not allowed (postgres)" \
  'CREATE FUNCTION public.g() RETURNS int LANGUAGE sql AS $$ SELECT 1 $$;'
evil "a table in another schema" "schema other than public" \
  'CREATE SCHEMA evil AUTHORIZATION classbeat_owner; CREATE TABLE evil.t (a int); ALTER TABLE evil.t OWNER TO classbeat_owner;'
evil "ALTER SYSTEM in a function body" "ALTER of a kind the schema does not use" \
  "CREATE FUNCTION public.h() RETURNS void LANGUAGE plpgsql AS \$\$ BEGIN ALTER SYSTEM SET archive_command = 'x'; END \$\$; ALTER FUNCTION public.h() OWNER TO classbeat_owner;"
evil "an untrusted function language" "function language" \
  "CREATE FUNCTION public.k() RETURNS void LANGUAGE c AS '\$libdir/plpgsql', 'plpgsql_call_handler'; ALTER FUNCTION public.k() OWNER TO classbeat_owner;"
docker rm -f "$scratch" > /dev/null; scratch=""

# The restore image: the source's, retagged for the destination project (no second build).
docker tag "$src:local" "$dst:local"
docker compose --profile '*' down -v --remove-orphans > /dev/null
echo "source stack and volume removed"

step "3. restore into the fresh project $dst"
use "$dst"
[ -z "$(docker volume ls -q --filter "name=^${dst}_db-data$")" ] || fail "the destination volume exists already"
rs "$dump"
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
