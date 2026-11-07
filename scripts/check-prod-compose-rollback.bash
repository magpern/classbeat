# shellcheck shell=bash
# The rollback test (--rollback-check OLD_VERSION, ADR 0020 decision 7).
# Sourced by check-prod-compose.sh, never run on its own; it shares that script's shell, variables and functions. Part of the check-prod-compose split (#763): no check lives here that is not in the original.
# --- Rollback test, no clone (ADR 0020 decision 7) -------------------------------------------------
# Going back to release OLD is safe only if the live database is at a revision OLD knows, i.e. the
# release you leave ran no migration OLD lacks. The database's Alembic revision is read from
# alembic_version; OLD's revisions are the migration files in its image (a revision's id is its file
# name). Unknown revision: OLD's `migrate` would fail ("Can't locate revision"); restore the backup.
rollback_verdict() {   # rollback_verdict OLD "REVISIONS (one per line)" "FILE LISTING (one per line)"
  local old="$1" revs="$2" files="$3" rev n=0
  while IFS= read -r rev; do
    [[ -n "$rev" ]] || continue
    n=$((n + 1))
    [[ "$rev" =~ ^[A-Za-z0-9_]+$ ]] || { echo "check-prod-compose: the database reports an odd revision; not rolling back" >&2; return 1; }
    grep -Fxq -- "$rev.py" <<<"$files" || {
      echo "check-prod-compose: the database is at revision $rev, which release $old does not have: its migrate would fail." >&2
      echo "check-prod-compose: do not set CLASSBEAT_VERSION back; restore the pre-update backup (runbook section 11, Rollback)." >&2
      return 1
    }
  done <<<"$revs"
  (( n > 0 )) || { echo "check-prod-compose: the database has no Alembic revision (alembic_version is empty)" >&2; return 1; }
  echo "rollback check: every database revision is known to release $old: no migration ran that it lacks"
}
rollback_check() {   # rollback_check OLD
  local old="$1" revs files
  revs="$(docker compose exec -T db psql -U postgres -d classbeat -X -At -c 'select version_num from alembic_version' 2>/dev/null)" \
    || { echo "check-prod-compose: cannot read the database revision (is db running? docker compose up -d db)" >&2; return 1; }
  files="$(docker run --rm --network none --entrypoint ls "ghcr.io/magpern/classbeat:$old" /app/migrations/versions 2>/dev/null)" \
    || { echo "check-prod-compose: cannot list the migrations of ghcr.io/magpern/classbeat:$old (docker login, then docker pull it)" >&2; return 1; }
  rollback_verdict "$old" "$revs" "$files"
}
if [[ -n "$rollback_old" ]]; then
  rollback_check "$rollback_old"
  exit $?
fi
