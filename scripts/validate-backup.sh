#!/usr/bin/env bash
# Check that a backup archive holds only what classbeat's own schema can produce (SR-06, #355).
#
#   scripts/validate-backup.sh ARCHIVE.dump
#
# This is defence in depth. The control is authentication: scripts/restore.sh only gets here with
# an archive that was signed by the expected key. This script then refuses an archive, even a
# correctly signed one, that could run anything but the application's schema and data as the
# database superuser. It runs pg_restore in a throwaway container with no network and no volume,
# never against a database, and reads the archive twice:
#
#   1. the table of contents (`pg_restore --list`) against an allowlist: header (custom format,
#      database classbeat), entry types (see TYPES), schema `public` (or none), owner one of
#      classbeat_owner, classbeat_queue, classbeat_maint, classbeat_app. Anything else is refused:
#      extensions, event triggers, languages, other schemas, foreign data wrappers, publications,
#      tablespaces, casts, operators, objects owned by the superuser. A migration that adds an
#      object type must add it here in the same PR; the backup drill fails until it does.
#   2. the SQL pg_restore would send (`pg_restore -f - --clean --if-exists`, the options restore.sh
#      uses, so the DROP statements stored in the archive are seen too), outside COPY data, against a denylist and
#      a few strict forms: no psql meta commands, no ALTER SYSTEM, no role, database, tablespace,
#      extension, event trigger, language, server or publication DDL, no DROP but the forms --clean writes, no DO or SET ROLE, no
#      role grants, no file or program access, only `LANGUAGE plpgsql` and `LANGUAGE sql`, only
#      the two SELECT forms pg_dump writes, COPY only as `COPY public.<table> (...) FROM stdin;`.
#      A denylist cannot be complete; it catches the obvious and the accidental. It reads the SQL
#      line by line, so a keyword split across lines, or a statement built at run time with EXECUTE
#      inside a function body, is not seen: the signature is the control, not this list.
#
# The image is the compose `db` service's postgres image (override with BACKUP_PG_IMAGE).
# Exit 0: accepted. Exit 1: refused, with every reason on stderr. Exit 2: usage or tool error.
set -euo pipefail

die() { echo "validate-backup.sh: $*" >&2; exit 2; }
[ $# -eq 1 ] && [ -f "$1" ] || die "usage: validate-backup.sh ARCHIVE.dump"
archive="$1"

image="${BACKUP_PG_IMAGE:-}"
if [ -z "$image" ]; then
  image="$(docker compose config --images 2> /dev/null | grep -m1 '^postgres:' || true)"
fi
[ -n "$image" ] || die "cannot tell the postgres image (set BACKUP_PG_IMAGE)"
pg_restore_in() { docker run --rm -i --network none --entrypoint pg_restore "$image" "$@" < "$archive"; }

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
pg_restore_in --list > "$work/toc" || { echo "validate-backup.sh: pg_restore cannot read the archive" >&2; exit 1; }
pg_restore_in -f - --clean --if-exists > "$work/sql" || { echo "validate-backup.sh: pg_restore cannot decode the archive" >&2; exit 1; }

refused=0
reason() { echo "validate-backup.sh: refused: $*" >&2; refused=1; }

# --- 1. table of contents -------------------------------------------------------------------
grep -qE '^;[[:space:]]+Format: CUSTOM$' "$work/toc" || reason "the archive is not in custom format"
grep -qE '^;[[:space:]]+dbname: classbeat$' "$work/toc" || reason "the archive is not a dump of database classbeat"
awk '
  BEGIN {
    # Longest first: a prefix test picks the first type that matches.
    n = split("TABLE DATA|SEQUENCE OWNED BY|SEQUENCE SET|FK CONSTRAINT|DEFAULT ACL|ROW SECURITY|" \
              "SEQUENCE|CONSTRAINT|FUNCTION|TRIGGER|COMMENT|SCHEMA|DEFAULT|POLICY|INDEX|TABLE|TYPE|ACL", T, "|")
    split("classbeat_owner classbeat_queue classbeat_maint classbeat_app", o, " ")
    for (i in o) owner[o[i]] = 1
  }
  /^;/ || /^[[:space:]]*$/ { next }
  {
    line = $0
    if (!sub(/^[0-9]+; [0-9]+ [0-9]+ /, "", line)) { print "unreadable table of contents line: " $0; bad = 1; next }
    type = ""
    for (i = 1; i <= n; i++) if (index(line, T[i] " ") == 1) { type = T[i]; break }
    if (type == "") { split(line, w, " "); print "entry type not allowed: " w[1] " (" line ")"; bad = 1; next }
    rest = substr(line, length(type) + 2)
    split(rest, f, " ")
    if (f[1] != "public" && f[1] != "-") { print "object outside schema public: " line; bad = 1 }
    if (type == "SCHEMA" && f[2] != "public") { print "schema other than public: " line; bad = 1 }
    last = line; sub(/.* /, "", last)
    if (!(last in owner)) { print "owner not allowed (" last "): " line; bad = 1 }
  }
  END { exit bad }
' "$work/toc" > "$work/toc.reasons" || true
if [ -s "$work/toc.reasons" ]; then
  while IFS= read -r l; do reason "$l"; done < <(head -n 20 "$work/toc.reasons")
fi

# --- 2. the SQL outside COPY data -----------------------------------------------------------
awk '
  function deny(msg) { print "line " NR ": " msg; bad = 1 }
  BEGIN { incopy = 0 }
  incopy { if ($0 == "\\.") incopy = 0; next }
  /^COPY / {
    if ($0 ~ /^COPY public\.[a-z0-9_]+ \([a-z0-9_, ]+\) FROM stdin;$/) { incopy = 1; next }
    deny("COPY in a form pg_dump does not write: " substr($0, 1, 80)); next
  }
  # postgres 17.11 brackets plain-text output with \restrict KEY and \unrestrict KEY (a psql guard
  # against meta commands in a dump); pg_restore sends neither to the server.
  /^\\(un)?restrict [A-Za-z0-9]+$/ { next }
  /^\\/ { deny("psql meta command"); next }
  /^[[:space:]]*--/ || /^[[:space:]]*$/ { next }
  $0 == "SELECT pg_catalog.set_config(\047search_path\047, \047\047, false);" { next }
  /^SELECT pg_catalog\.setval\(\047public\.[a-z0-9_]+\047, [0-9]+, (true|false)\);$/ { next }
  # --clean makes pg_restore send the DROP statements stored in the archive: only these forms.
  /^DROP (INDEX|TABLE|SEQUENCE) IF EXISTS public\.[a-z0-9_]+;$/ { next }
  /^DROP (POLICY|TRIGGER) IF EXISTS [a-z0-9_]+ ON public\.[a-z0-9_]+;$/ { next }
  /^DROP FUNCTION IF EXISTS public\.[a-z0-9_]+\([a-z0-9_ ,\[\]]*\);$/ { next }
  /^ALTER TABLE IF EXISTS ONLY public\.[a-z0-9_]+ DROP CONSTRAINT IF EXISTS [a-z0-9_]+;$/ { next }
  /^ALTER TABLE IF EXISTS ONLY public\.[a-z0-9_]+ ALTER COLUMN [a-z0-9_]+ DROP DEFAULT;$/ { next }
  /^SELECT/ { deny("a SELECT pg_dump does not write: " substr($0, 1, 80)); next }
  {
    s = tolower($0)
    if (s ~ /(^|[^a-z0-9_])alter[[:space:]]+(system|role|user|group|database|event|server|foreign|extension|language|tablespace|publication|subscription|operator|collation|conversion|text|large|aggregate)([^a-z0-9_]|$)/) deny("ALTER of a kind the schema does not use")
    if (s ~ /(^|[^a-z0-9_])create[[:space:]]+(or[[:space:]]+replace[[:space:]]+)?(role|user|group|database|event|server|foreign|extension|language|trusted|procedural|tablespace|publication|subscription|cast|operator|aggregate|rule|access|transform|conversion|collation|text|statistics|procedure|materialized|view|temp|temporary|unlogged|domain|type)([^a-z0-9_]|$)/) deny("CREATE of a kind the schema does not use")
    if (s ~ /(^|[^a-z0-9_])drop([^a-z0-9_]|$)/) deny("DROP")
    if (s ~ /(^|[^a-z0-9_])do[[:space:]]+(\$|language|\047)/) deny("DO block")
    if (s ~ /(^|[^a-z0-9_])(set|reset)[[:space:]]+(local[[:space:]]+|session[[:space:]]+)?(role|session[[:space:]]+authorization)([^a-z0-9_]|$)/) deny("SET ROLE or SESSION AUTHORIZATION")
    if (s ~ /(^|[^a-z0-9_])copy([^a-z0-9_]|$).*(program|[[:space:]]to[[:space:]])/) deny("COPY to a program or file")
    if (s ~ /(^|[^a-z0-9_])(pg_read_file|pg_read_binary_file|pg_ls_dir|pg_stat_file|lo_import|lo_export|lo_from_bytea|lo_put|dblink|pg_terminate_backend|pg_cancel_backend|pg_reload_conf|pg_sleep|set_config|pg_logical_[a-z_]+|pg_create_[a-z_]+|pg_drop_[a-z_]+|pg_execute_server_program|pg_switch_wal|pg_promote)([^a-z0-9_]|$)/) deny("a server function the schema does not use")
    if (s ~ /^grant[[:space:]]+[^[:space:]]+[[:space:]]+to[[:space:]]/) deny("GRANT of a role")
    if (s ~ /(^|[^a-z0-9_])on[[:space:]]+(database|tablespace|parameter|language|foreign|large|all[[:space:]]+(tables|sequences|functions)[[:space:]]+in)([^a-z0-9_]|$)/) deny("a GRANT or REVOKE on an object kind the schema does not use")
    if (s ~ /(^|[^a-z0-9_])on[[:space:]]+schema[[:space:]]+/ && s !~ /(^|[^a-z0-9_])on[[:space:]]+schema[[:space:]]+public([^a-z0-9_]|$)/) deny("a privilege on a schema other than public")
    if (s ~ /(^|[^a-z0-9_])(pg_catalog|information_schema|pg_temp|pg_toast)\./ && s !~ /pg_catalog\.(set_config|setval)/ && s !~ /::pg_catalog\.|pg_catalog\.[a-z0-9_]+ *\(/ ) deny("a reference to a system schema")
    if (match(s, /(^|[^a-z0-9_])language[[:space:]]+[a-z0-9_]+/)) {
      l = substr(s, RSTART, RLENGTH); sub(/.*language[[:space:]]+/, "", l)
      if (l != "plpgsql" && l != "sql") deny("function language " l)
    }
    if (match(s, /(^|[^a-z0-9_])owner[[:space:]]+to[[:space:]]+[a-z0-9_"]+/)) {
      l = substr(s, RSTART, RLENGTH); sub(/.*owner[[:space:]]+to[[:space:]]+/, "", l)
      if (l !~ /^(classbeat_owner|classbeat_queue|classbeat_maint|classbeat_app)$/) deny("ownership given to " l)
    }
  }
  END { exit bad }
' "$work/sql" > "$work/sql.reasons" || true
if [ -s "$work/sql.reasons" ]; then
  while IFS= read -r l; do reason "$l"; done < <(head -n 20 "$work/sql.reasons")
fi

if [ "$refused" != 0 ]; then
  echo "validate-backup.sh: the archive is refused; nothing was restored" >&2
  exit 1
fi
echo "validate-backup.sh: archive accepted ($(grep -cvE '^;|^$' "$work/toc") entries)"
