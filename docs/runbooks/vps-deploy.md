# VPS deploy (M6): Caddy, compose.prod.yaml

Status: **configuration ready, not deployed.** Everything here is the owner's step: deploying,
DNS, TLS, the server and the mail account are in the safety envelope's "only with the owner's
authorization" list (AGENTS.md). Agents verified the files locally (see "Local smoke"); they never
touched a server, DNS or a provider. No real host name or credential is in the repository: every
`example.test` below is a placeholder.

What is deployed: `compose.prod.yaml` (Caddy, web, worker, db, and the one-shot migrate and
db-functions) from the release image `ghcr.io/magpern/classbeat:X.Y.Z`, with the `Caddyfile`.

```
internet -> :80/:443 caddy -(edge)-> web:8000 -(backend, internal)-> db
                                     worker -(backend)-> db ; worker -(egress)-> model providers, MOWL, Spotify
```

Only `caddy` publishes ports. `edge` is an ordinary (not internal) network, so `web` also has
outbound internet by design (SMTP, OAuth and Spotify token calls); `worker` goes out over `egress`. `db` has no port at all, and there is no mailpit (mail goes to a
real SMTP service). `backend` is an internal Docker network: `db`, `migrate` and `db-functions`
have no route out.

## 1. Server prerequisites

- A Linux VPS (amd64 or arm64; the image is both) with Docker Engine and Compose v2, a non-root
  sudo user, SSH by key only (password login off), automatic security updates.
- Time sync on (certificates and sessions depend on it).
- Disk: the Postgres volume, plus room for the json-file logs (at most 50 MB per container).
- A mail account for the app (SMTP host, port 587 with STARTTLS or 465 with TLS, a login, a
  sender address on a domain you control, SPF and DKIM set at the mail provider).
- The release you deploy exists: `gh run list --workflow release.yml`, tag `vX.Y.Z`.

## 2. Firewall

Allow inbound only: `22/tcp` (SSH, ideally from your address), `80/tcp` (certificate challenge
and the redirect to https) and `443/tcp`, `443/udp` (HTTP/3). Set this in the **provider's
firewall** (outside the server): ports Docker publishes bypass the host's `ufw`, so a host
firewall alone does not stop a later compose mistake from exposing `db`. The compose file
publishes only Caddy's ports, and `docker ps` must show no other `0.0.0.0` mapping (section 8).

## 3. Get the files

The server needs `compose.prod.yaml`, `Caddyfile` and `docker/postgres/` of the release you run.
The repository is public:

```bash
sudo mkdir -p /opt/classbeat && sudo chown "$USER" /opt/classbeat
git clone https://github.com/magpern/classbeat.git /opt/classbeat
cd /opt/classbeat && git checkout vX.Y.Z      # detached on the release tag, never main
```

## 4. Pull the private image from GHCR

The package `ghcr.io/magpern/classbeat` is private (never change that). On the server, once:

1. On GitHub create a **fine-grained or classic token** with only `read:packages` for the
   package, owned by you, expiring in 90 days. Use a token of its own, not your admin token.
2. `echo "$TOKEN" | docker login ghcr.io -u <github-user> --password-stdin` (type the token
   into the shell without it ending up in history: `read -rs TOKEN` first, then `unset TOKEN`).
   Docker keeps it in `~/.docker/config.json` of the deploy user (mode 600).
3. `docker pull ghcr.io/magpern/classbeat:X.Y.Z` must work. Put the token's expiry in your
   calendar: an expired token breaks the next update, not the running site.

Never use `latest` (there is none); `CLASSBEAT_VERSION` pins a release.

## 5. Secrets and settings

Secrets are files in `/opt/classbeat/secrets/` (directory mode 700, files 600, owned by the
deploy user), mounted as Docker secrets. They are never in `.env`, never committed
(`secrets/` and `.env` are git-ignored).

```bash
cd /opt/classbeat && mkdir -p secrets && chmod 700 secrets
umask 077
for n in postgres_password database_owner_password database_password database_maint_password; do
  head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n' > "secrets/$n"
done
head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n' > secrets/app_secret_key      # >= 32 bytes
head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n' > secrets/connection_kek      # exactly 64 hex characters
```

Then by hand (type or paste, no `echo` of a real value into the shell history):

- `secrets/smtp_password`: the SMTP login password (an empty file if the SMTP account needs none).
- `secrets/anthropic_api_key`: the Anthropic key. The worker refuses to start in prod without it.
- `secrets/openai_api_key`, `secrets/deepseek_api_key`: leave **empty** until the owner decides
  (ADR 0012, [m3c-model-enable.md](m3c-model-enable.md)); the files must exist.
- OAuth sign-in providers, if wanted, are not part of this file: they need their client id
  variable and `oauth_<provider>_client_secret` secret file added to `compose.prod.yaml` by a
  reviewed change.

**Back up `secrets/connection_kek` and `secrets/postgres_password` and the other secret files
off the server now** (a password manager or an encrypted offline copy). Losing `connection_kek`
makes every stored MOWL and Spotify connection unreadable (ADR 0004); the database backup is no
use without it.

`/opt/classbeat/.env` (not committed), placeholders replaced by your values:

```
CLASSBEAT_VERSION=X.Y.Z
CLASSBEAT_DOMAIN=classbeat.example.test
ACME_EMAIL=you@example.test
SMTP_HOST=smtp.example.test
SMTP_SENDER=noreply@classbeat.example.test
# optional: SMTP_PORT=587  SMTP_TLS=starttls  SMTP_USERNAME=...  ADMIN_EMAILS=you@example.test
# optional: SIGNUP_MODE=invite  CONNECTION_KEK_ID=kek-1  MODEL_PROVIDER=anthropic
```

Song picking stays off (its default) until the owner runs [m4-live-activation.md](m4-live-activation.md).

## 6. DNS and TLS (the owner's steps)

1. At your DNS provider create an `A` record (and an `AAAA` if the server has IPv6) for
   `CLASSBEAT_DOMAIN` pointing at the server. Wait until `dig +short classbeat.example.test`
   answers with it.
2. Nothing else for TLS: Caddy asks Let's Encrypt for a certificate by itself on first start,
   over port 80 or 443, and renews it. If DNS is not right yet the first start only logs failed
   challenges; fix DNS and `docker compose -f compose.prod.yaml restart caddy`. Let's Encrypt
   rate-limits repeated failures, so check DNS before the first start. The certificate lives in
   the `caddy-data` volume: do not delete it casually.
3. HSTS is sent as `max-age=31536000` (one year) without `includeSubDomains` or `preload`. Once
   the site has run well, adding either is a deliberate later change: they cannot be undone
   quickly.

## 7. First deploy

```bash
cd /opt/classbeat
docker compose -f compose.prod.yaml config -q          # every required variable is set
docker compose -f compose.prod.yaml up -d --wait
docker compose -f compose.prod.yaml ps
```

`up` runs `migrate`, then `db-functions`, then starts `web`, `worker` and `caddy`; the Postgres
init (roles) runs on the first start of the empty volume. Create the first admin and invite:

```bash
docker compose -f compose.prod.yaml exec web python -m classbeat invite create --expires 7d
docker compose -f compose.prod.yaml exec web python -m classbeat admin grant you@example.test
```

(`admin grant` needs the account to exist: sign up with the invite first.)

## 8. Post-deploy checklist

- [ ] `curl -sI https://$DOMAIN/api/ready` is `200`; `http://$DOMAIN/` redirects to https.
- [ ] Response headers on `/`, `/api/ready` and an SSE stream carry, once each:
      `Content-Security-Policy` (the app's: `default-src 'self'; ...`),
      `Referrer-Policy: no-referrer`, `Strict-Transport-Security`, `X-Content-Type-Options`.
- [ ] `docker ps --format '{{.Names}} {{.Ports}}'`: only caddy shows a host mapping
      (`0.0.0.0:80`, `0.0.0.0:443`); `db` and `web` show `5432/tcp`, `8000/tcp` only. From another
      machine, ports 5432, 8000, 8025 and 2019 are closed (`nc -vz $IP 5432`).
- [ ] No mailpit container exists.
- [ ] Sign up with the invite, receive the verification mail (check spam, SPF, DKIM).
- [ ] `docker compose -f compose.prod.yaml exec web ls /run/secrets` lists no model key;
      `exec worker` lists `anthropic_api_key`. `exec web touch /app/x` fails (read-only).
- [ ] A design run shows live progress in the browser (SSE passes Caddy unbuffered).
- [ ] The app sees the real client address, not Caddy's (Caddy at `172.29.80.10` is the only
      trusted proxy, `CLASSBEAT_TRUSTED_PROXIES`): the per-IP limits of two different networks
      do not share a bucket. Over IPv6 Docker's userland proxy shows the network gateway as the
      client, so all IPv6 visitors share one rate-limit bucket (not a spoofing risk). Fixing it
      needs `ip6tables` in the Docker daemon **and** `enable_ipv6` with an IPv6 subnet on the
      `edge` network; the compose file does not do this.
- [ ] `docker compose -f compose.prod.yaml logs caddy` access lines show no query string and no
      cookie or authorization header.
- [ ] Run an external header/TLS check of the domain (for example SSL Labs, securityheaders.com).
- [ ] Backups run (section 11) and a restore has been tried.
- [ ] Certificate expiry is monitored (Caddy renews at 30 days left; an external uptime check
      that also reads the certificate date is worth having).

## 8a. Request body size (every proxy in front of the app)

The app refuses a request body over 64 KiB with 413 on every route (`classbeat.bodylimit`, #351,
threat model T102), but a proxy should stop an oversized body first so it never reaches the app's
network buffers. Caddy does (`request_body { max_size 1MB }` in the `Caddyfile`). **Any other proxy
needs the same cap, and the home beta behind SWAG does:** SWAG is nginx and ships
`client_max_body_size 0` (unlimited). In its `nginx.conf` or the site's proxy config set

```nginx
client_max_body_size 1m;
```

then reload it, and check from outside (a 2 MB file; the answer must be `413`, and a small body must not be):

```bash
head -c 2000000 /dev/zero > /tmp/big.bin
curl -s -o /dev/null -w '%{http_code}
' -X POST --data-binary @/tmp/big.bin "https://$DOMAIN/api/auth/login"
```

The app logs each refusal it makes (method, path without the query, status and client address; at most
one line per address every 10 seconds, with the number it skipped), so a flood that reaches it is visible.
Without this the app still answers 413 and does not buffer more than the cap, but the proxy
buffers the whole body first (nginx to disk or memory) before it does.

## 9. Update

```bash
cd /opt/classbeat
git fetch --tags && git checkout vNEW             # the release you want
sed -i 's/^CLASSBEAT_VERSION=.*/CLASSBEAT_VERSION=NEW/' .env   # NEW = the tag without the v
docker compose -f compose.prod.yaml pull
docker compose -f compose.prod.yaml up -d --wait
```

`up` re-runs `migrate` (idempotent) and `db-functions` before it replaces `web` and `worker`;
the worker has 30 s to hand its leases back (ADR 0005). **Take a database dump first** (section 11),
always: it is the rollback. Read the release notes for owner-facing steps.

## 10. Rollback

Take the dump of section 11 before every update. Migrations are not run backwards: the old
image's `migrate` service does `alembic upgrade head`, and a database stamped with a revision the
old image does not know makes it fail ("Can't locate revision"), so `db-functions`, `web` and
`worker` never start. Setting `CLASSBEAT_VERSION` back is therefore **only** safe when the
release you are leaving added no migration (compare the revision files between the two tags:
`git diff --stat vOLD vNEW -- backend/migrations/versions`). Some migrations also drop or reshape
data, so treat none as reversible.

- **No migration in the release:** set `CLASSBEAT_VERSION` back to the previous tag,
  `git checkout vOLD`, `docker compose -f compose.prod.yaml up -d --wait`.
- **A migration ran** (any kind): restore the pre-update dump, then start the old version.
  ```bash
  docker compose -f compose.prod.yaml down                       # keeps volumes
  docker volume rm "$(docker compose -f compose.prod.yaml config --format json | jq -r .name)_db-data"
  git checkout vOLD && sed -i 's/^CLASSBEAT_VERSION=.*/CLASSBEAT_VERSION=OLD/' .env
  docker compose -f compose.prod.yaml up -d --wait db            # empty volume: init creates the roles
  docker compose -f compose.prod.yaml run --rm migrate           # the OLD schema and the remaining roles
  docker compose -f compose.prod.yaml run --rm db-functions
  docker compose -f compose.prod.yaml exec -T db pg_restore -U postgres -d classbeat     --clean --if-exists < "$BACKUP_DIR/classbeat-YYYY-MM-DD.dump"
  docker compose -f compose.prod.yaml up -d --wait
  ```
  Anything written since the dump is lost; tell the testers. (Deleting `db-data` here is the one
  deliberate exception to the rule below, and only after the dump file is safe off the volume.)
  An agent rehearsed exactly this sequence locally on a throwaway stack (a marker row survived
  the dump, `docker volume rm`, the steps above, and `web` came up healthy; `pg_restore` printed
  no error). Restoring straight into the empty database without `migrate` and `db-functions` first
  fails on the roles those create. It is not the full M6 restore drill: rehearse it on your own
  data once.

Never delete `db-data` or `caddy-data` otherwise to "reset" a production stack.

## 10a. Key-compromise procedure (`maint`)

The dev `compose.yaml` has a `maint` service (ADR 0004 section 2, `keys wipe`) that
`compose.prod.yaml` does not. Adding it, with the owner role's secret and the SMTP settings, is a
follow-up for the M6 issues; until then that procedure has no prepared command on the VPS.

## 11. Backups (schedule hook)

The backup job and the restore drill are M6's backup issue; this is the hook it plugs into, not
the final design. A logical dump of the database, run by cron on the server as the deploy user:

```bash
# /etc/cron.d/classbeat-backup  (03:15 every night); BACKUP_DIR is off-volume and then copied off-server
15 3 * * * deploy cd /opt/classbeat && docker compose -f compose.prod.yaml exec -T db \
  pg_dump -U postgres -d classbeat --format=custom > "$BACKUP_DIR/classbeat-$(date +\%F).dump"
```

Ciphertext only leaves the database (tokens are encrypted with `connection_kek`), but the dump
still holds personal data: encrypt it before it leaves the server and delete old ones on a
schedule (GDPR retention). A dump restores into an empty stack with `pg_restore` after the
Postgres init has created the roles. The secret files are backed up separately (section 5).

## 12. Local smoke (what agents ran; no server involved)

Reproduces the verification without a server, a domain or a certificate authority. Caddy uses its
internal CA for `localhost`; no real name or credential is needed.

```bash
docker build -t ghcr.io/magpern/classbeat:smoke .           # the image the release would publish
scripts/worktree.sh init                                     # fills secrets/ if missing
touch secrets/smtp_password                                  # the one prod-only secret file
export COMPOSE_PROJECT_NAME=classbeat-smoke CLASSBEAT_VERSION=smoke CLASSBEAT_DOMAIN=localhost \
  ACME_EMAIL=smoke@example.test SMTP_HOST=smtp.example.test SMTP_SENDER=noreply@example.test \
  CADDY_HTTP_PORT=20080 CADDY_HTTPS_PORT=20443
docker run --rm -e ACME_EMAIL -e CLASSBEAT_DOMAIN -v "$PWD/Caddyfile:/etc/caddy/Caddyfile:ro" \
  caddy:2.10.2 caddy validate --config /etc/caddy/Caddyfile
docker compose -f compose.prod.yaml up -d --wait caddy web   # db, migrate, db-functions come with it
curl -ik --resolve localhost:20443:127.0.0.1 https://localhost:20443/api/ready
docker compose -f compose.prod.yaml down -v
```

The worker is not started in the smoke: `fake` is refused in prod and a real key is not wanted.
On Docker Desktop for Windows the TLS handshake over IPv4 to the published port can hang
(`curl -6`, or `docker compose exec caddy wget --no-check-certificate https://localhost/...`,
work); that is the desktop port relay, not the configuration.

`scripts/check-prod-compose.sh` (also a CI step) runs the config, invariant and `caddy validate`
checks with dummy values.

What was checked this way (2026-10-31), and what was not:

- `caddy validate` and `caddy fmt` pass; `docker compose config` passes.
- Through Caddy with internal TLS: the app's CSP, `Referrer-Policy`, HSTS, `nosniff`,
  `Permissions-Policy`, COOP/CORP on `/` and `/api/ready`, the CSP exactly once; `http://` answers
  308 to https; a 2 MB body is 413 at Caddy and a 500 KB body passes the proxy (the app then answers 413 for it: its own cap is 64 KiB, section 8a); a client-sent
  `X-Forwarded-For` is replaced by Caddy's own.
- SSE: a stand-in upstream writing one event per second (with `Accept-Encoding: gzip`) reached the
  client one event per second, not compressed. The real `/api/jobs/<id>/events` needs a signed-in
  user and a job and was **not** run through the real app here; the first design run in the
  post-deploy checklist is that check.
- `db` has no host port, `backend` is an internal network, web is read-only with no capabilities,
  and web mounts no model key. The worker's start-up and the real mail path were not run.
