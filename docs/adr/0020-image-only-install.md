# ADR 0020: Image-only install (a server needs an image, a compose file and settings)

Status: Proposed (issue #519)

## Context

`docs/runbooks/vps-deploy.md` section 3 clones the repository on the server and checks out a
tag, because `compose.prod.yaml` bind-mounts repository files that are not in the app image:
`docker/postgres/init` (the roles), `docker/postgres/queue` and `docker/postgres/maint` (the job
queue's functions and the admin role, run by the one-shot `db-functions`), and the runbook runs
`scripts/check-prod-compose.sh` and the secret-file readability probe from that checkout.
A clone on the server is more than the server needs: it holds the whole source, `git` must be
there, an update is a `git checkout` of a tag next to a `docker compose pull`, and the files
the stack runs are not the files the release run built and verified.

Constraints that stay: the app image holds no secret except Swedebeat's app key from the
release workflow's BuildKit secret (ADR 0015 section 4); the package is private and stays so
(AGENTS.md "Releasing"); `db`, `db-functions` and `postgres_password` are on the pinned
`postgres:17.11`, because the queue functions' md5 (`classbeat.db.queue_digests`) is
computed on it; the secret files keep their setup (`<deploy user>:10010`, mode 640, folder 2700;
#511); agents never deploy.

## Decision

1. **The SQL ships in a small database image**, `ghcr.io/magpern/classbeat-db:X.Y.Z`, built in
   the release run from `docker/postgres/Dockerfile`: `FROM postgres:17.11` (the same exact
   tag as `compose.yaml`, the CI services and the queue digests) plus `COPY` of
   `docker/postgres/init` to `/docker-entrypoint-initdb.d`, `queue` to `/queue` and `maint` to
   `/maint`. `compose.prod.yaml` uses it for `db` and `db-functions` and bind-mounts nothing:
   the only mounts are the named volume and a tmpfs. It holds SQL and a shell script, no
   secret. Same tags as the app image (`X.Y.Z`, `X.Y` for a release, `sha-<short>`), both
   platforms, provenance and SBOM. `CLASSBEAT_VERSION` pins both images.
   *Alternatives:* putting the SQL in the app image needs `psql` there (the image is
   `python:slim`; adding the postgres client enlarges and changes the app image for a one-shot
   job); release assets for the SQL would still be mounted from the host, which keeps the
   bind mounts and the allow-list of them in the check; Postgres on a different base image
   would change the queue digests. The dev `compose.yaml` keeps its bind mounts (a checkout).
   The `docker/postgres` change filter already marks a change as `backend`.
2. **`compose.prod.yaml` and the other deploy files are assets of the GitHub Release**, attached
   by the release run from the tag's own files: `compose.prod.yaml`, `compose.prod.loopback.yaml`,
   `compose.prod.lan.yaml` (home/LAN, below), `check-prod-compose.sh`,
   `swag-classbeat.subdomain.conf`, `prod.env.example` and `SHA256SUMS` (their checksums).
   `scripts/release-assets.sh` is the single list; CI builds the assets from it on every pull
   request that touches it, and a test pins the list. The assets are public (the repository
   is); the images are private. No asset is generated or edited at release time, so the file
   on the server is the file the tag's CI checked. Assets are published after both images,
   so a release with an asset has its images. Backup and restore scripts stay in the
   repository for now (they run from a checkout of the operator's choice; a follow-up may ship
   them) and the runbook says so.
3. **The checks run from the released script.** `check-prod-compose.sh` detects its place: in a
   checkout it is the CI check it is today; next to a `compose.prod.yaml` with no `docs/`
   (the install folder) it is the pre-deploy check. There it validates the shipped files
   with dummy values (so a local edit of a compose file is caught), renders the real `.env`,
   checks the real `PROXY_IP` and the env file for a client secret, and checks the real secret
   files and the folder. `--probe` additionally runs, per service that mounts secrets, a
   `test -r` of every file from the pulled image (what the runbook did by hand; prints
   `<service>: <file> OK|FAIL`, never a value) and fails on any `FAIL`. The mutation
   self-tests and the nginx check stay checkout-only.
4. **The one-time sudo step is checked, not only documented.** A fresh install needs `sudo`
   once for the group and the folder (`groupadd` if the number is free, then
   `chgrp 10010 secrets && chmod 2700 secrets`), because an owner outside the group cannot set
   the group or `g+s` itself. The check now also requires the folder to be group 10010 with
   the setgid bit, and every failure prints the exact command that fixes it and says which
   need sudo. No change to how secrets are mounted or read: `group_add: ["10010"]`, uid
   10001 / `postgres`, `./secrets/<name>` files. Per Amendment 13 the PR that touches the
   secret checks gets one final Opus review of its head.
5. **Home/LAN variant**: an overlay `compose.prod.lan.yaml` for swag on another machine. web
   leaves the `proxy` network and is published on `${WEB_LAN_ADDRESS}:${WEB_HOST_PORT:-8080}`
   (one private IPv4 address of this server, required; never `0.0.0.0`, never loopback),
   and `PROXY_IP` is swag's own LAN address, the one trusted peer (with the iptables DNAT a
   published port keeps the sender's address, so web sees swag, not the Docker gateway). The
   boundary is the host firewall: Docker's rules bypass `ufw`, so the runbook gives a
   `DOCKER-USER` rule that drops everything but `PROXY_IP` to that port. The check refuses a
   published port that is not exactly that one address, a `0.0.0.0` or IPv6 wildcard bind, a
   trusted proxy other than `PROXY_IP`, and `proxy` still attached. Consequence: swag to web is
   plain HTTP across the LAN (session cookies and forwarded headers in clear), acceptable on a
   private home network the owner controls, not elsewhere; the runbook says so.
6. **Install, update and rollback** (the runbook has them in full):
   - *Install:* create `/opt/classbeat`, download the assets of `vX.Y.Z` with `curl` and check
     `SHA256SUMS`; `cp prod.env.example .env` and fill it; create `secrets/` (one-time sudo,
     then the loop of runbook section 5); `docker login ghcr.io` once (a token with only
     `read:packages`, expiry in the calendar); `./check-prod-compose.sh --install` (default in the install
     folder), `docker compose pull`, `./check-prod-compose.sh --probe`, `docker compose up -d --wait`.
   - *Update:* back up; download the new release's assets over the old ones (the old files are
     kept as `*.prev`), `sha256sum -c`, `CLASSBEAT_VERSION=NEW` in `.env`, check, `pull`,
     `up -d --wait`. The compose file and both images always come from the same release.
   - *Rollback:* the previous release's assets and `CLASSBEAT_VERSION=OLD`; as before,
     only when the release you leave ran no migration, otherwise restore the backup.
7. **Release workflow changes are minimal and backwards compatible**: a second image job
   (`db-image`, parallel to `image`) and the release job checks out the tag, builds the asset
   folder with `scripts/release-assets.sh` and passes the files to the same
   `gh release create`. The existing `image` job, tags and secret handling are untouched.
   No secret reaches an artifact: the assets are repository files and the db image's context
   is `docker/postgres` only. Verified in CI by actionlint, shellcheck and
   `scripts/test-release-assets.sh`; the first real run is the next tag, which the owner or
   Architect pushes.

## Pull requests

| PR | Scope | Review |
|---|---|---|
| IO1 (#565) | `classbeat-db` image: Dockerfile, release job, compose.prod uses it, CI build and content check, check script drops the bind-mount allow-list for "no bind mounts" | Sonnet |
| IO2 (#566) | Release assets (`release-assets.sh`, release job, test) and the install mode, `--probe` and clear sudo hints of the check script | Sonnet, then one final Opus review of the head (secret checks, Amendment 13) |
| IO3 (#567) | `compose.prod.lan.yaml` and its checks | Sonnet |
| IO4 (#568) | Runbook sections 3, 4, 9, 11 and the rest without `git clone`, README and AGENTS.md pointers | Sonnet |

## Consequences

- A server needs Docker, a folder with six small files and `.env`, and a GHCR token; no git.
- A new moving part: the db image must exist for the compose file to run, so a release whose
  `db-image` job failed publishes no assets (the release job needs both image jobs).
- The first deploy after this change pulls the db image; the existing data volume is
  untouched (same Postgres major and minor, init scripts only run on an empty volume).
- A second private package (`classbeat-db`). The token's `read:packages` covers both.
- Backup and restore scripts are not yet released assets.
