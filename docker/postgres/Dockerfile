# syntax=docker/dockerfile:1
# The database image of a release (ADR 0020, #565): the pinned postgres plus the SQL the stack runs
# on it, so a server needs no checkout. `db` uses it as the database (the init scripts run on the
# first start of an empty volume) and `db-functions` as the one-shot that applies the job queue
# functions and the admin role (queue/db-functions.sh).
#
# The base tag must equal compose.yaml's `db` image and the CI services' (the queue functions' md5 in
# backend/src/classbeat/db/queue_digests.py is computed on it); the `checks` job compares them. Build
# context: docker/postgres. It holds SQL and one shell script, never a secret: passwords are read at
# run time from Docker secret files.
FROM postgres:17.11

# Trivy fails the image scan on zlib1g (HIGH CVE-2026-76844, CVE-2026-85091; fixed in 1.3.1-1+deb13u1)
# and the pinned postgres:17.11 still ships the old one. Upgrade only that package. Remove this
# block when the postgres base image is bumped to one that carries the fix.
USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends --only-upgrade zlib1g \
    && rm -rf /var/lib/apt/lists/*

COPY init /docker-entrypoint-initdb.d
COPY queue /queue
COPY maint /maint
# Whatever file modes the checkout had: readable by the postgres user (uid 999) the services run as.
RUN chmod -R a+rX,go-w /docker-entrypoint-initdb.d /queue /maint
# Not root: compose runs both services as postgres anyway (the shared group 10010 must survive, so no
# gosu re-exec from a root entrypoint), and the image says so by itself.
USER postgres
