#!/bin/sh
# Entrypoint of the one-shot compose service `db-functions` (ADR 0005 section 3): run
# 20-queue.sql as the superuser, in one transaction, after the migrations; then the admin role
# classbeat_maint (ADR 0011 section 5, docker/postgres/maint) in a second one.
#
# The superuser password is read from the postgres_password secret file and written to a
# PGPASSFILE on a tmpfs (/run/pgpass, mode 0700), never to the environment or a command line, so
# it never shows in `docker inspect` and never lands in the container's writable layer.
set -eu

secret=/run/secrets/postgres_password
pgpass=/run/pgpass/pgpass
if [ ! -s "$secret" ]; then
  echo "db-functions: the postgres_password secret is missing or empty" >&2
  exit 1
fi

umask 077
# Like the postgres image's POSTGRES_PASSWORD_FILE: the file's content without trailing newlines.
# In a .pgpass field, backslash and colon are escaped with a backslash.
password="$(cat "$secret")"
escaped="$(printf '%s' "$password" | sed -e 's/\\/\\\\/g' -e 's/:/\\:/g')"
unset password
printf 'db:5432:classbeat:postgres:%s\n' "$escaped" > "$pgpass"
unset escaped
export PGPASSFILE="$pgpass"

psql --single-transaction -v ON_ERROR_STOP=1 -X -q -w \
  -h db -U postgres -d classbeat -f /queue/20-queue.sql

# The admin role (ADR 0011 section 5): its attributes, settings and column allowlist, then its
# password from the database_maint_password secret, in one more transaction.
if [ ! -s /run/secrets/database_maint_password ]; then
  echo "db-functions: the database_maint_password secret is missing or empty" >&2
  exit 1
fi
exec psql --single-transaction -v ON_ERROR_STOP=1 -X -q -w \
  -h db -U postgres -d classbeat -f /maint/30-maint.sql -f /maint/31-maint-password.psql
