# ADR 0020: Image-only install (a server needs an image, a compose file and settings)

Status: Proposed (issue #519)

## Context

`docs/runbooks/vps-deploy.md` section 3 clones the repository on the server and checks out a
tag, because `compose.prod.yaml` bind-mounts repository files that are not in the app image:
`docker/postgres/init` (the roles), `docker/postgres/queue` and `docker/postgres/maint` (the job
queue's functions and the admin role, run by the one-shot `db-functions`), and the runbook runs
`scripts/check-prod-compose.sh` and the secret-file readability probe from that checkout.
A clone on the server is more than the server needs: it holds the whole source, `git` must be
there, and an update is a `git checkout` of a tag next to a `docker compose pull`.

Constraints that stay: the app image holds no secret except Swedebeat's app key from the
release workflow's BuildKit secret (ADR 0015 section 4); the packages are private and stay so
(AGENTS.md "Releasing"); `db`, `db-functions` and `postgres_password` are on the pinned
`postgres:17.11`, because the queue functions' md5 (`classbeat.db.queue_digests`) is
computed on it; the secret files keep their setup (`<deploy user>:10010`, mode 640, folder 2700;
#511); agents never deploy. No install exists yet (the runbook says "configuration ready, not
deployed"), so nothing has to migrate from a clone.

## Decision

1. **The SQL ships in a small database image**, `ghcr.io/magpern/classbeat-db:X.Y.Z`, built in
   the release run from `docker/postgres/Dockerfile`: `FROM postgres:17.11` plus `COPY` of
   `docker/postgres/init` to `/docker-entrypoint-initdb.d`, `queue` to `/queue` and `maint` to
   `/maint`. The files are root-owned and readable by all, never writable by group or others
   (`chmod -R a+rX,go-w`); no exec bit is needed, because `db-functions` runs
   `sh /queue/db-functions.sh`. The image ends with `USER postgres` (Trivy DS-0002), and compose
   keeps `user: postgres` on both services so the group 10010 survives (no gosu re-exec).
   `compose.prod.yaml` uses it for `db` and `db-functions` and bind-mounts nothing: the only
   mounts are the named volume and a tmpfs. It holds SQL and a shell script, no secret. Same tags
   as the app image (`X.Y.Z`, `X.Y` for a release, `sha-<short>`), both platforms, provenance and
   SBOM. `CLASSBEAT_VERSION` pins both images.
   CI keeps the Postgres pin single: the `checks` job requires the Dockerfile's `FROM` to equal
   compose.yaml's `db` image and the CI services' (otherwise Dependabot could bump the Dockerfile
   alone and the queue md5 would drift), builds the image and diffs its SQL with the repository's.
2. **`compose.prod.yaml` and the other deploy files are assets of the GitHub Release**, attached
   by the release run from the tag's checkout, after both images exist (so a release with an asset
   has its images). `scripts/release-assets.sh` is the single list and is explicit, never a glob:
   `compose.prod.yaml`, `compose.prod.loopback.yaml`, `compose.prod.lan.yaml`,
   `check-prod-compose.sh`, `swag-classbeat.subdomain.conf`, `prod.env.example`, `backup.sh`,
   `restore.sh`, `validate-backup.sh` and `SHA256SUMS`. Every asset except `SHA256SUMS` is a
   committed, reviewed repository file copied byte for byte; `SHA256SUMS` is generated at release
   time from them. `.env`, `secrets/` and the owner's own `compose.bridge.yaml`
   (`proton-bridge-smtp.md`) are never assets. The restore drill script stays a developer tool.
   A test pins that the runbook's download loops list exactly the assets.
3. **What the integrity story is, plainly.** `SHA256SUMS` comes from the same release over the same
   connection as the files, so `sha256sum -c` catches a truncated or corrupted download and
   **nothing else**; it does not protect against tampering. Trust in the assets is GitHub's TLS
   plus write access to the repository and its releases, the same trust the `git clone` of a tag
   needed. The downloaded `check-prod-compose.sh` that validates the compose file comes from the
   same place, so it cannot vouch for itself; it catches honest mistakes and local edits, as it
   always did (it is not a security boundary, AGENTS.md). The images are pulled by version
   (tags are mutable): the runbook records each pulled image's digest (`docker image inspect`) next
   to the install so a later pull can be compared.
   **Amendment (#583):** the release workflow now attests every asset (`SHA256SUMS` included) and
   both images' digests with `actions/attest` (GitHub artifact attestations; the images' are not
   pushed to the registry), in a job of their own that has `id-token` and `attestations` but no
   secret (the image job, which holds the Swedebeat key, gets neither), and a failed attestation
   publishes no release. Each image digest is in the release notes. The runbook verifies assets by file
   and images by `oci://` with `gh attestation verify -R magpern/classbeat` before the first `up`.
   A verifier cannot vouch for itself: `check-prod-compose.sh` does not verify its own attestation;
   `gh` does, first, and `gh` is a trusted install. The existing `provenance: mode=max` stays as the
   buildx registry attestation. Still **unverified** until a real tag runs: the commands' exact output
   and whether `gh` needs a registry login for the private image.
4. **The checks run from the released script.** `check-prod-compose.sh` detects its place, with
   one rule: next to a `compose.prod.yaml` with no `docs/` (the install folder) it is the
   pre-deploy check; in a checkout it is the CI check it was. `--install` exists only to force
   the install checks in a checkout (the tests use it); the runbook never passes it. In the
   install folder it validates the shipped files with dummy values (so a local edit is caught),
   renders the real `.env`, checks the real `PROXY_IP`, where web is published, the env file for a
   client secret, and the real secret files and their folder. `--probe` additionally runs, per
   service that mounts secrets, a `test -r` of every file from the pulled image (prints
   `<service>: <file> OK|FAIL`, never a value) and fails on any `FAIL`. `--rollback-check OLD`
   (below) is the third mode. The mutation self-tests and the nginx check stay checkout-only.
   Downloaded files lose the exec bit: the runbook runs `chmod +x` (or `bash check-prod-compose.sh`).
5. **The one-time sudo step is checked, not only documented.** A fresh install needs `sudo`
   once for the group and the folder (`groupadd` if the number is free, then
   `chgrp 10010 secrets && chmod 2700 secrets`), because an owner outside the group cannot set
   the group or `g+s` itself. The check requires the folder to be group 10010 with the setgid
   bit, and every failure prints the exact command that fixes it and says which need sudo. No
   change to how secrets are mounted or read: `group_add: ["10010"]`, uid 10001 / `postgres`,
   `./secrets/<name>` files. Per Amendment 13 the PR that touches the secret checks gets one final
   Opus review of the head that is enqueued.
6. **Home/LAN variant**: an overlay `compose.prod.lan.yaml` for swag on another machine. web
   leaves the `proxy` network and is published on `${WEB_LAN_ADDRESS}:${WEB_HOST_PORT:-8080}`
   (one private IPv4 address of this server, required; never `0.0.0.0`, never loopback), and
   `PROXY_IP` is the one trusted peer. Precisions:
   - `PROXY_IP` is "swag's own LAN address" only if swag has an address of its own on the LAN
     (host network, macvlan). Swag in a bridge container on host Y reaches web from Y's address
     (SNAT), so every process and container on Y becomes a trusted proxy that can name any client
     address; the runbook says to give swag its own address or accept that.
   - Restricting the port is the operator's choice (owner, 2026-11-05: installation security is
     the operator's; nothing in classbeat requires or configures a host firewall). The runbook
     gives an optional example: Docker's rules bypass `ufw`, and in `DOCKER-USER` the packet
     already has the container's destination port, so a rule must match the original port
     (`-m conntrack --ctorigdstport 8080 --ctdir ORIGINAL`) and drop everything but `PROXY_IP`.
   - That a published port keeps the sender's address (so web sees swag, not the Docker gateway)
     holds for the iptables DNAT path and is **to be confirmed on the server** with the existing
     413 / logged-address test of the runbook's checklist, as 6b was on Docker 29.
   - The check refuses a published port that is not exactly that one private address, a wildcard
     or IPv6 bind, a trusted proxy other than `PROXY_IP`, `PROXY_IP` equal to the server's own
     address, and `proxy` still attached.
   Consequence: swag to web is plain HTTP across the LAN (cookies and forwarded headers in clear),
   acceptable on a private home network the owner controls, not elsewhere. Without a firewall
   rule, the impact is limited to other LAN hosts reaching web under their own address.
7. **Install, update and rollback** (the runbook has them in full):
   - *Install:* create `/opt/classbeat`; download the assets of `vX.Y.Z` with
     `curl --proto '=https' -fsSL` and check `SHA256SUMS` (Decision 3 says what that proves);
     `chmod +x` the scripts; `cp prod.env.example .env` and fill it; create `secrets/` (one-time
     sudo, then the loop of runbook section 5); `docker login ghcr.io` once (a **classic**
     personal access token with only `read:packages`, expiry in the calendar; such a token reads
     every package its account can see, so use an account of its own. That fine-grained tokens
     cannot read GHCR is **unverified** here); `./check-prod-compose.sh`, `docker compose pull`
     (record the digests), `./check-prod-compose.sh --probe`, `docker compose up -d --wait`.
   - *Update:* back up; download the new release's assets into a **staging folder**, verify
     `SHA256SUMS` there, and only then move them over the old ones (the old files are kept in
     `previous-OLD/`); `CLASSBEAT_VERSION=NEW` in `.env`, check, `pull`, `up -d --wait`. The compose
     file and both images always come from the same release. `db` is recreated (Postgres restarts)
     on every update, because its image reference changes with `CLASSBEAT_VERSION`; the data volume
     is kept.
   - *Rollback:* needs no clone. Before going back to `OLD`, `./check-prod-compose.sh --rollback-check OLD`
     compares the live database's Alembic revision (`alembic_version`) with the revisions the
     `OLD` app image carries (the migration files in its `/app/migrations/versions`; a revision's id
     is its file name). If the database's revision is one `OLD` knows, the release you leave ran no
     migration `OLD` lacks and setting `CLASSBEAT_VERSION` back with `previous-OLD/` is safe; if it
     is not, `OLD`'s `migrate` would fail, and the only way back is to restore the pre-update backup
     (as before). The first release that carries assets has no earlier release with assets: going
     back below it needs that tag's checkout once.
8. **Release workflow changes are minimal and backwards compatible**: a second image job
   (`db-image`, parallel to `image`) and the release job checks out the tag, builds the asset
   folder with `scripts/release-assets.sh` and passes the files to the same
   `gh release create`. The existing `image` job, tags and secret handling are untouched. No
   secret reaches an artifact: the assets are repository files and the db image's context is
   `docker/postgres` only. Verified in CI by actionlint, shellcheck and
   `scripts/test-install-check.sh`; the first real run is the next tag, which the owner or
   Architect pushes.

## Alternatives

- **SQL in the app image.** It needs `psql` there (`python:slim`); adding the postgres client
  enlarges and changes the app image for a one-shot job. Rejected.
- **SQL as release assets, bind-mounted.** Keeps the host bind mounts and their allow-list in the
  check, and the files on the host are then unverified inputs to the database container. Rejected.
- **Postgres on another base image or version.** Changes the queue digests. Rejected.
- **Run the check and the probe from the app image** (the #519 option). The check needs
  `docker compose`, `jq`, the host's `.env` and `secrets/` metadata and the Docker daemon, which
  the app image has none of and must not get. A released script runs on the host where those are.
- **A `fetch-release.sh` helper for download and update.** Another unverified script to
  bootstrap; the runbook's short loop does the same. Rejected for now.
- **Keep the clone.** What this replaces: it needs `git`, a whole source tree and a checkout
  per update.
- **A single combined image for app and database.** Two processes, two lifecycles, and the
  database must stay on the pinned Postgres image. Rejected.

## Pull requests

Each is its own issue and PR. #573 goes first; #575, #576 and #578 are stacked on each other's
branches, and after a squash each needs a merge of `main` (a new head: the Architect's merge check,
or a scoped review if a conflict is resolved by hand) before it is enqueued.

| PR | Scope | Review |
|---|---|---|
| IO1 (#565) | `classbeat-db` image: Dockerfile, `db-image` release job, compose.prod uses it, CI build, SQL diff and `FROM` pin check, the check script drops the bind-mount allow-list for "no bind mounts" | Sonnet |
| IO2 (#566) | `scripts/release-assets.sh` with six assets (`compose.prod.yaml`, `compose.prod.loopback.yaml`, `check-prod-compose.sh`, the swag sample, `prod.env.example`, `SHA256SUMS`), `test-install-check.sh`, the release job's asset step, install mode, `--probe` and the sudo hints of the check script | Sonnet, then one final Opus review of the enqueued head (secret checks, Amendment 13) |
| IO3 (#567) | `compose.prod.lan.yaml` and its checks; adds the overlay to the asset list and the test | Sonnet |
| IO4 (#568) | Runbook sections 3, 4, 9, 11 and the rest without `git clone`; adds `backup.sh`, `restore.sh` and `validate-backup.sh` to `release-assets.sh` and the test, with the test that pins the runbook's download loops; `--rollback-check` in the check script; AGENTS.md pointers. The Architect confirms #567 and #568 change no secret check, or they need Opus too | Sonnet |

## Consequences

- A server needs Docker, a folder with the release's files and `.env`, and a GHCR token; no git.
- Integrity is no better than the clone it replaces (Decision 3): `SHA256SUMS` catches truncation
  only; with `gh`, the attestations (#583, Decision 3) catch tampering of the assets and images.
- A new moving part: the db image must exist for the compose file to run, so a release whose
  `db-image` job failed publishes no assets (the release job needs both image jobs).
- The first deploy after this change pulls the db image; the data volume is untouched (same
  Postgres version, init scripts only run on an empty volume).
- A second private package (`classbeat-db`); a classic `read:packages` token covers both.
- The restore drill script is not an asset; the owner's `compose.bridge.yaml` is not either.
