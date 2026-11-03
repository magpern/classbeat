# Server deploy (M6): compose.prod.yaml behind swag

Status: **configuration ready, not deployed.** The server is the owner's own machine; DNS for
`classbeat.pernemark.se` is in Cloudflare and points at it; TLS and routing are the owner's
[swag](https://docs.linuxserver.io/general/swag/) (linuxserver/swag, nginx) container. Everything
here is the owner's step: deploying, DNS, TLS and the mail account are in the safety envelope's "only
with the owner's authorization" list (AGENTS.md). **Agents cannot reach the server.** The deploy is
run from a local session on that machine (the owner's terminal, or a Claude Code session the owner
starts there), one step at a time with the owner's go-ahead. Agents checked the files offline
(`scripts/check-prod-compose.sh`, section 13); they never touched the server, DNS, Cloudflare or a provider.

What is deployed: `compose.prod.yaml` (web, worker, db, and the one-shot migrate and db-functions)
from the release image `ghcr.io/magpern/classbeat:X.Y.Z`, plus one site file in swag
([swag-classbeat.subdomain.conf](swag-classbeat.subdomain.conf)). Caddy, the edge of the first
version of this runbook (#341), was removed in #492: swag already owns ports 80 and 443 on this
machine, and a second, unused edge config would only drift.

```
visitor -> (Cloudflare, if proxied) -> :443 swag -(proxy, internal)-> web:8000 -(backend, internal)-> db
                                                   web -(web-out)-> SMTP, OAuth and Spotify token endpoints
                                                   worker -(backend)-> db ; worker -(egress)-> model providers, MOWL, Spotify
```

No classbeat service publishes a port. `proxy` is a Docker network the owner creates once and that
only swag and web join; swag has a fixed address on it (`PROXY_IP`). `backend` is internal: `db`,
`migrate` and `db-functions` have no route out and `db` has no port. There is no mailpit (mail goes
to a real SMTP service, or Proton Mail Bridge, [proton-bridge-smtp.md](proton-bridge-smtp.md)).

## 1. Prerequisites

- Docker Engine with **Compose 2.24.4 or later** (`docker compose version`), a non-root user in the
  `docker` group for the deploy, SSH (if any) by key only, automatic security updates, time sync on.
- swag running and serving another site already (its certificate covers `classbeat.pernemark.se`:
  a wildcard, or the name in `SUBDOMAINS`/`EXTRA_DOMAINS`), its config volume at hand (`/config`).
- Disk for the Postgres volume and the json-file logs (at most 50 MB per container).
- A mail account for the app (SMTP host, 587 with STARTTLS or 465 with TLS, a login, a sender on a
  domain you control, SPF and DKIM), or Bridge.
- The release you deploy exists: `gh run list --workflow release.yml`, tag `vX.Y.Z`.

## 2. What is reachable from the internet

Only swag: the router forwards `443/tcp` (and `80/tcp` if swag answers HTTP challenges or redirects)
to swag and nothing else. Ports Docker publishes bypass the host's `ufw`, so the router (or the
upstream firewall) is the real boundary: never forward 5432, 8000 or a classbeat port. With the
loopback overlay (section 6b) web listens on `127.0.0.1` only. `docker ps` must show no classbeat
container with a host mapping other than that (section 9).

## 3. Get the files

The server needs `compose.prod.yaml` (and `compose.prod.loopback.yaml` for section 6b),
`docker/postgres/` and the swag sample of the release you run. The repository is public:

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

Secrets are files in `/opt/classbeat/secrets/`, mounted as Docker secrets. They are never in
`.env`, never committed (`secrets/` and `.env` are git-ignored).

**Who can read them (#511).** Outside Swarm, Docker Compose bind-mounts a file secret with the host
file's owner and mode and ignores `uid`, `gid` and `mode`, so a file only the deploy user can read
(mode 600) is `Permission denied` inside the containers: `web`, `worker` and `migrate` run as uid
10001, `db` and `db-functions` as `postgres` (uid 999). Every service that mounts a secret therefore
gets the supplementary group **10010** (`group_add` in `compose.prod.yaml`), and every file in
`secrets/` is owned by the deploy user, **group 10010, mode 640**: the deploy user writes it, the
containers read it through the group, nobody else on the host reads it. Each container only sees
the files compose mounts into it. The folder is mode 2700 and group 10010: only the deploy user
enters it, and the setgid bit gives every new file in it group 10010 by itself. Never make a secret
file world-readable (644), and put no account on the host into group 10010.

One-time, with sudo (the group name is only a label; the number is what counts, and it must not
already belong to something else):

```bash
getent group 10010 || sudo groupadd --system --gid 10010 classbeat-secrets   # must have no members
cd /opt/classbeat && mkdir -p secrets && sudo chgrp 10010 secrets && sudo chmod 2700 secrets   # sudo: an owner outside the group cannot set g+s
```

Then as the deploy user (no sudo: the files get group 10010 from the folder, mode 640 from the umask):

```bash
cd /opt/classbeat
umask 027
for n in postgres_password database_owner_password database_password database_maint_password; do
  head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n' > "secrets/$n"
done
head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n' > secrets/app_secret_key      # >= 32 bytes
head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n' > secrets/connection_kek      # exactly 64 hex characters
for n in smtp_password anthropic_api_key openai_api_key deepseek_api_key swedebeat_app \
  oauth_google_client_secret oauth_github_client_secret oauth_microsoft_client_secret; do
  [ -e "secrets/$n" ] || install -m 640 /dev/null "secrets/$n"   # empty placeholder; fill in below
done
ls -ln secrets/          # every file: <your uid> 10010, -rw-r-----
```

Files made before this change (or anywhere else and moved in) keep their old group and mode; fix
them once with `sudo chgrp 10010 secrets/* && chmod 640 secrets/*`. `scripts/check-prod-compose.sh`
checks every file named in `compose.prod.yaml` on the server: a regular file (not a link), group
10010, the group may read and not write, nothing for others, and the folder closed to others
(it prints names, modes and group numbers only, never a file's content).

Type or paste each value, with no `echo` of a real value into the shell history, for example
`( umask 027; read -rs S && printf '%s' "$S" > secrets/anthropic_api_key; unset S )`.

Fill in these files by hand (writing into an existing file keeps its group and mode):

- `secrets/smtp_password`: the SMTP login password (an empty file if the SMTP account needs none).
- `secrets/anthropic_api_key`: the Anthropic key. The worker refuses to start in prod without it.
- `secrets/openai_api_key`, `secrets/deepseek_api_key`: leave **empty** until the owner decides
  (ADR 0012, [m3c-model-enable.md](m3c-model-enable.md)); the files must exist.
- `secrets/swedebeat_app`: leave **empty** (the loop above made it); the file must exist
  because `compose.yaml` and `compose.prod.yaml` mount it into `web`, and compose fails at `up`
  without it. A release image holds Swedebeat's key itself; the file is only a runtime override
  ([swedebeat-connect.md](swedebeat-connect.md)).
- `secrets/oauth_google_client_secret`, `secrets/oauth_github_client_secret`,
  `secrets/oauth_microsoft_client_secret`: the OAuth client secrets, mounted into `web` only. The
  loop above creates them **empty** (that provider is off) and never empties one that exists; the
  files must exist, or `up` fails with a missing-file error. To switch Google on, section 5a.

A file that holds only blanks counts as empty. If an optional file (`smtp_password`, the three
`oauth_*_client_secret`, `swedebeat_app`) cannot be read, `web` starts without it and logs
`secret file <name> is not readable; treated as not set` (that feature is off); any other unreadable
secret stops the service that needs it. Either way, fix the group and mode as above.

**Back up `secrets/connection_kek` and `secrets/postgres_password` and the other secret files
off the server now** (a password manager or an encrypted offline copy). Losing `connection_kek`
makes every stored MOWL and Spotify connection unreadable (ADR 0004); the database backup is no
use without it.

`/opt/classbeat/.env` (not committed). `COMPOSE_FILE` makes every plain `docker compose` command
in this runbook, and `scripts/backup.sh` / `restore.sh`, use the production files; nothing below
needs `-f`.

```
COMPOSE_FILE=compose.prod.yaml
COMPOSE_PROJECT_NAME=classbeat-prod              # scripts/restore.sh refuses the default name `classbeat`
# with Proton Mail Bridge: COMPOSE_FILE=compose.prod.yaml:compose.bridge.yaml   (proton-bridge-smtp.md)
# with a proxy on the host: COMPOSE_FILE=compose.prod.yaml:compose.prod.loopback.yaml   (section 6b)
# both: COMPOSE_FILE=compose.prod.yaml:compose.prod.loopback.yaml:compose.bridge.yaml   (in this order)
CLASSBEAT_VERSION=X.Y.Z
CLASSBEAT_DOMAIN=classbeat.pernemark.se          # web gets CLASSBEAT_PUBLIC_BASE_URL=https://classbeat.pernemark.se
PROXY_IP=172.29.90.10                            # swag's fixed address on the proxy network (section 6)
# optional: PROXY_NETWORK=classbeat-proxy        # the network's name, if you chose another
SMTP_HOST=smtp.example.test
SMTP_SENDER=noreply@classbeat.pernemark.se
# optional: SMTP_PORT=587  SMTP_TLS=starttls  SMTP_USERNAME=...  ADMIN_EMAILS=you@example.test
# optional: SMTP_CA_FILE=/run/smtp-ca/bridge.pem   (pinned self-signed SMTP certificate, see proton-bridge-smtp.md)
# optional: SIGNUP_MODE=invite  CONNECTION_KEK_ID=kek-1  MODEL_PROVIDER=anthropic
# optional: OAUTH_GOOGLE_CLIENT_ID=...apps.googleusercontent.com  OAUTH_VISIBLE_PROVIDERS=google   (section 5a)
# optional: OAUTH_GITHUB_CLIENT_ID=...  OAUTH_MICROSOFT_CLIENT_ID=...   (client ids only; never a client secret here)
```

`CLASSBEAT_PUBLIC_BASE_URL` is the one public origin (ADR 0003 section 4): the CSRF origin check,
mail links, the Spotify callback, the OAuth sign-in redirect URI (`<base>/oauth/<provider>/callback`,
section 5a) and the passkey RP ID come from it, never from a request header.
Prod refuses anything but `https://`. **With `SMTP_CA_FILE` set, `compose.bridge.yaml` must be in
`COMPOSE_FILE`**, or `up` and every restart recreate `web` with the variable but no mounted file,
and `web` refuses to start.

Song picking stays off (its default) until the owner runs [m4-live-activation.md](m4-live-activation.md).

### 5a. Sign in with Google (optional)

A provider is on only when **both** its client id (`.env`) and its secret file hold a value; with
either empty it is off. Nothing about it is ever typed into a chat, an issue or git.

1. In Google Cloud Console (APIs & Services, Credentials), create an **OAuth client ID** of type
   *Web application*. Authorized redirect URI: `https://<host>/oauth/google/callback`, where
   `<host>` is `CLASSBEAT_DOMAIN`. The app builds the redirect URI from `CLASSBEAT_PUBLIC_BASE_URL`
   (`https://` + `CLASSBEAT_DOMAIN`), and one instance serves **one** base URL: registering both
   `https://classbeat.eu/oauth/google/callback` and `https://classbeat.pernemark.se/oauth/google/callback`
   at Google is fine, but `CLASSBEAT_DOMAIN` must be the host visitors actually use (and swag serves),
   or Google refuses the sign-in with `redirect_uri_mismatch` and the passkeys and mail links point
   elsewhere. The consent screen needs only the `openid` and `email` scopes; the app asks for
   `auth_time` itself (no other registration change).
2. Put the client id in `/opt/classbeat/.env` (it is not secret, it appears in the browser's URL):
   ```
   OAUTH_GOOGLE_CLIENT_ID=1234567890-abc.apps.googleusercontent.com
   OAUTH_VISIBLE_PROVIDERS=google          # the default; which enabled providers get a button
   ```
3. Put the client secret in its file, never in `.env` (`scripts/check-prod-compose.sh` refuses a
   `*CLIENT_SECRET*` variable there). `secrets/` is git-ignored and never committed. Paste it without
   it reaching the shell history or the screen:
   ```bash
   cd /opt/classbeat
   ( umask 027; read -rs S && printf '%s' "$S" > secrets/oauth_google_client_secret; unset S )
   ls -ln secrets/oauth_google_client_secret          # <your uid> 10010, -rw-r----- like the others
   ```
4. Recreate web (only web reads these; the worker never gets them):
   ```bash
   scripts/check-prod-compose.sh
   docker compose up -d --wait web
   ```
5. Test while the consent screen is in **Testing** mode: only the test users listed there (Google
   Cloud Console, OAuth consent screen, Test users) can sign in. Open the site in a private window:
   *Continue with Google* is shown on the sign-in and sign-up screens; a test user signs in (a new
   account still needs an invite while `SIGNUP_MODE=invite`). Publishing the app to every Google
   account later needs a verified domain and public privacy policy and terms pages on it (Google's
   verification), which is a separate step.

**Provider off** (no client id, or an empty secret file): no Google button, `GET /api/auth/oauth/providers`
lists no `google`, and starting a Google sign-in answers 404 `provider_disabled`; email and password
sign-in work as before. To switch Google off again, empty the file (`: > secrets/oauth_google_client_secret`)
or remove the client id, then `docker compose up -d --wait web`. GitHub and Microsoft work the same way
with `OAUTH_GITHUB_CLIENT_ID` / `OAUTH_MICROSOFT_CLIENT_ID` and their secret files (redirect URI
`https://<host>/oauth/github/callback` or `/oauth/microsoft/callback`; Microsoft also needs the
`auth_time` optional claim, AGENTS.md), and appear on the screens only when named in
`OAUTH_VISIBLE_PROVIDERS`.

## 6. The proxy network and the client address

The app's per-IP rate limits (sign-in, sign-up, mail, MOWL connect), its auth events and the body
cap's log use the client address (ADR 0003 section 9): the TCP peer, unless the peer is in
`CLASSBEAT_TRUSTED_PROXIES`; then the right-most `X-Forwarded-For` entry that is not a trusted
proxy. **The decision (#492):**

- web trusts **exactly one** peer, swag, at a fixed private address (`PROXY_IP`). Not a range: any
  other container on that network, the host, and Cloudflare are not trusted. In prod, web refuses
  to start unless the trusted proxy is one private address (a range, a list, a public address or a
  name is refused). `scripts/check-prod-compose.sh` refuses the same in its dummy rendering and, when
  `PROXY_IP` is set in the shell or in the checkout's `.env`, checks that real value too (one
  private IPv4 address); run it on the server before the first `up` (section 9).
- swag sends `X-Forwarded-For: $remote_addr`, **overwriting** what the visitor sent, and clears
  `X-Real-IP`, `CF-Connecting-IP`, `True-Client-IP`, `Forwarded`, `X-Forwarded-Host`.
- Behind Cloudflare's proxy, swag restores `$remote_addr` from `CF-Connecting-IP` only when the
  TCP peer is one of Cloudflare's published ranges (nginx real-ip, non-recursive; section 8). A
  connection straight to the origin keeps its own address, whatever headers it sends.
- The app never reads `CF-Connecting-IP` and never lists Cloudflare's ranges.

So nobody on the internet can choose the address the app uses: at most a visitor that bypasses
Cloudflare is counted under its own address. `backend/tests/test_trusted_proxy_swag.py` plays these
requests against the app.

### 6a. Shared Docker network (the default)

Once, pick two `/24`s that clash with nothing on the machine or the LAN (`docker network ls`,
`docker network inspect`, `ip route`): web's own `web-out` is fixed at `172.29.80.0/24` in
`compose.prod.yaml`; the example below uses `172.29.90.0/24` for `proxy`.

```bash
docker network create --internal --subnet 172.29.90.0/24 --ip-range 172.29.90.128/25 --gateway 172.29.90.1 classbeat-proxy
```

`--internal`: the network carries swag-to-web traffic only (swag keeps its own default network for
the internet; web goes out over `web-out`). `--ip-range`: Docker hands out addresses to containers
without a fixed one (web, anything attached by mistake) only from `.128` to `.255`, so swag's fixed
`.10` can never be given to another container, even while swag is stopped or being recreated.
If you pick another subnet, keep swag's address inside it and outside the `--ip-range`. Then give swag a fixed address on it, in swag's own
compose file (the owner's, not in this repository):

```yaml
services:
  swag:
    # ... as it is today ...
    networks:
      default: {}
      classbeat-proxy:
        ipv4_address: 172.29.90.10   # = PROXY_IP in /opt/classbeat/.env
networks:
  classbeat-proxy:
    external: true
```

and recreate swag (`docker compose up -d swag` in swag's directory). If swag already has a
`networks:` list, keep every entry and add this one. **Attach nothing else to `classbeat-proxy`.**
Check: `docker network inspect classbeat-proxy` shows `"IPRange": "172.29.90.128/25"`, lists swag at
`172.29.90.10` and, after section 9, the classbeat web container (at `.128` or above), nothing else.

### 6b. Alternative: web on 127.0.0.1 (a proxy on the host)

For an nginx that runs on the host itself (or swag with `network_mode: host`), which cannot join a
Docker network: add `compose.prod.loopback.yaml` to `COMPOSE_FILE` (section 5). web is then
published on `127.0.0.1:${WEB_HOST_PORT:-8080}` only, leaves the `proxy` network, and trusts
`172.29.80.1`, the `web-out` gateway, because Docker's port relay connects from there (checked on
Docker 29: a request to the published loopback port reaches the container from the gateway). Any
process on the host that can reach the port can then name a client; the internet cannot. Keep
`PROXY_IP` set (any private address, for example `172.29.80.1`: compose needs the variable, the
overlay replaces its use). No `docker network create` is needed. In the swag sample, use
`set $upstream_classbeat 127.0.0.1;` and port `8080` in both `proxy_pass` lines.

## 7. The swag site config

1. Copy [swag-classbeat.subdomain.conf](swag-classbeat.subdomain.conf) to
   `/config/nginx/proxy-confs/classbeat.subdomain.conf` in swag's volume. Its `server_name` is
   already `classbeat.pernemark.se` (it must equal `CLASSBEAT_DOMAIN`).
2. Cloudflare proxied (section 8): install the swag mod that writes Cloudflare's ranges to
   `/config/nginx/cf_real-ip.conf` (`DOCKER_MODS=linuxserver/mods:swag-cloudflare-real-ip` on the
   swag container), or write that file yourself from <https://www.cloudflare.com/ips/>, one
   `set_real_ip_from <range>;` per line, and refresh it when Cloudflare changes the list. DNS only:
   delete the `include /config/nginx/cf_real-ip.conf;` and `real_ip_header` lines instead.
3. Check swag's `/config/nginx/ssl.conf` and `nginx.conf`: the optional `add_header
   Content-Security-Policy` and `Referrer-Policy` lines must stay **commented** (the app sends its
   own; nginx would send both). If one of the five headers the sample adds (HSTS, `nosniff`,
   `Permissions-Policy`, COOP, CORP) is already active there, delete it from the sample so it is
   sent once. Do not use swag's `proxy.conf` for this site: it appends to the visitor's
   `X-Forwarded-For` instead of replacing it (the app would still pick swag's entry, but the sample
   does not depend on that).
4. `docker exec swag nginx -t && docker exec swag nginx -s reload`.

What the sample does, so a later edit keeps it: proxies to `classbeat-web:8000` (web's alias on
the proxy network, resolved per request so swag starts while classbeat is down); the progress
stream `GET /api/jobs/{id}/events` unbuffered, uncompressed, with a 900 s read timeout (the stream
lives 600 s and writes every 15 s; the app also sends `X-Accel-Buffering: no`);
`client_max_body_size 1m` (section 10); the forwarded headers of section 6; an access log of method,
path, status and size, **without the query string** (OAuth codes) and without cookies; HSTS
`max-age=31536000` without `includeSubDomains` or `preload` (adding either later is deliberate: they
cannot be undone quickly). HTTP to HTTPS redirects are swag's default server. No location sets a
`proxy_set_header` or `add_header` of its own (that would silently drop the server-level ones);
the check script refuses one.

## 8. Cloudflare

Either mode works; **which one is the owner's decision.**

- **DNS only (grey cloud):** visitors connect to swag directly; the peer is the visitor. Remove the
  two real-ip lines from the sample (section 7). Nothing else.
- **Proxied (orange cloud):** visitors connect to Cloudflare, Cloudflare to swag.
  - SSL/TLS mode **Full (strict)** (swag has a valid certificate). Never Flexible: Cloudflare would
    talk HTTP to the origin, and the app's `__Host-` cookies and `https` origin assume HTTPS end to end.
  - Keep the real-ip lines (section 7), so the app sees the visitor, not Cloudflare. Without them
    every visitor shares a Cloudflare address per edge, which no one can forge but which makes the
    per-IP limits trip for everyone at once.
  - Leave off anything that rewrites pages: Rocket Loader, Email Address Obfuscation, Mirage, Zaraz
    and other injected scripts break under the app's CSP (no inline script, `'self'` only). Leave
    Cloudflare's own HSTS off (swag sends it) and do not add Transform Rules that change or remove
    `Content-Security-Policy`, `Referrer-Policy` or `Set-Cookie`.
  - Caching: the default (static file extensions only) is fine; never a "Cache Everything" rule on
    `/api/*` or `/`. The SSE stream passes Cloudflare (it writes every 15 s, inside Cloudflare's
    100 s idle limit).
  - Optional hardening: allow 443 at the router/firewall only from Cloudflare's ranges, or turn on
    Authenticated Origin Pulls and verify Cloudflare's client certificate in the sample
    (`ssl_client_certificate` + `ssl_verify_client on`), so the origin cannot be reached around
    Cloudflare. Not needed for the client address (section 6), useful against direct floods.

## 9. First deploy

```bash
cd /opt/classbeat
docker compose config -q            # every required variable is set (COMPOSE_FILE from .env)
scripts/check-prod-compose.sh       # needs jq; the real PROXY_IP in .env, and secrets/ group 10010 mode 640
docker compose pull
# Every service reads each secret it mounts: one line "<service>: <file> OK" or "... FAIL" per
# file, never a value (`test -r` asks the kernel and opens nothing). Any FAIL: fix the group and
# mode (section 5) before `up`.
for s in db migrate db-functions web worker; do
  docker compose run --rm --no-deps -T --entrypoint sh "$s" -c \
    'for f in /run/secrets/*; do if test -r "$f"; then echo "${f##*/} OK"; else echo "${f##*/} FAIL"; fi; done' \
    | sed "s/^/$s: /"
done
docker compose up -d --wait
docker compose ps
```

`up` runs `migrate`, then `db-functions`, then starts `web` and `worker`; the Postgres init (roles)
runs on the first start of the empty volume. Create the first admin and invite:

```bash
docker compose exec web python -m classbeat invite create --expires 7d
docker compose exec web python -m classbeat admin grant you@example.test
```

(`admin grant` needs the account to exist: sign up with the invite first.)

### Post-deploy checklist

- [ ] `curl -sI https://classbeat.pernemark.se/api/ready` is `200`; `http://` redirects to https.
- [ ] Response headers on `/`, `/api/ready` and an SSE stream carry, **once each**:
      `Content-Security-Policy` (the app's: `default-src 'self'; ...`),
      `Referrer-Policy: no-referrer`, `Strict-Transport-Security`, `X-Content-Type-Options`.
- [ ] `docker ps --format '{{.Names}} {{.Ports}}'`: no classbeat container has a host mapping (with
      6b: web shows `127.0.0.1:8080->8000/tcp` only); `db` and `web` show `5432/tcp`, `8000/tcp`.
      From another machine, ports 5432 and 8000 (and 8080) are closed (`nc -vz $IP 5432`).
- [ ] `docker network inspect classbeat-proxy` (6a): swag at `PROXY_IP` and classbeat's web only.
- [ ] No mailpit container exists.
- [ ] Sign up with the invite, receive the verification mail (check spam, SPF, DKIM).
- [ ] The readability loop in section 9 printed `OK` for every file and no `FAIL`;
      `ls -ln secrets/` shows group `10010` and `-rw-r-----` (or stricter) on every file, and
      `scripts/check-prod-compose.sh` passes on the server.
- [ ] `docker compose exec web ls /run/secrets` lists no model key; `exec worker` lists
      `anthropic_api_key` and no `oauth_*_client_secret`. `exec web touch /app/x` fails (read-only).
- [ ] A design run shows live progress in the browser (SSE passes swag, and Cloudflare, unbuffered).
- [ ] **The app sees your real address, and a forged header changes nothing.** From outside the
      LAN (a phone hotspot), send a body over the app's 64 KiB cap but under swag's 1m with forged
      headers; the app answers 413 and logs the address it used:
      ```bash
      head -c 100000 /dev/zero | curl -s -o /dev/null -w '%{http_code}\n' \
        -H 'X-Forwarded-For: 6.6.6.6' -H 'CF-Connecting-IP: 6.6.6.6' -H 'X-Real-IP: 6.6.6.6' \
        --data-binary @- https://classbeat.pernemark.se/api/auth/login       # 413
      docker compose logs --since 2m web | grep 'request body refused'      # its client_ip
      ```
      The logged address must be the hotspot's public IP (`curl -s https://ifconfig.me` from that
      device), never `6.6.6.6`, never swag's `172.29.90.10`, never a Cloudflare address.
- [ ] swag's `/config/log/nginx/classbeat.access.log` lines show no query string and no cookie.
- [ ] Run an external header/TLS check of the domain (for example SSL Labs, securityheaders.com).
- [ ] Backups run (section 12) and a restore has been tried.

## 10. Request body size

The app refuses a request body over 64 KiB with 413 on every route (`classbeat.bodylimit`, #351,
threat model T103), but the proxy should stop an oversized body first so it never reaches the app's
network buffers. swag ships `client_max_body_size 0` (unlimited); the sample sets
`client_max_body_size 1m;` for this site. Check from outside (a 2 MB body must be `413` from swag; a
small one must not):

```bash
head -c 2000000 /dev/zero > /tmp/big.bin
curl -s -o /dev/null -w '%{http_code}\n' -X POST --data-binary @/tmp/big.bin "https://classbeat.pernemark.se/api/auth/login"
```

The app logs each refusal it makes (method, path without the query, status and client address; at most
one line per address every 10 seconds, with the number it skipped), so a flood that reaches it is visible.

## 11. Update

**Take a backup first** (section 12), always: it is the rollback.

```bash
cd /opt/classbeat
git fetch --tags && git checkout vNEW             # the release you want
( umask 027; touch secrets/swedebeat_app \
  secrets/oauth_google_client_secret secrets/oauth_github_client_secret secrets/oauth_microsoft_client_secret )
scripts/check-prod-compose.sh                     # secrets/ files: group 10010, mode 640 or stricter
sed -i 's/^CLASSBEAT_VERSION=.*/CLASSBEAT_VERSION=NEW/' .env   # NEW = the tag without the v
docker compose pull
docker compose up -d --wait
```

`up` re-runs `migrate` (idempotent) and `db-functions` before it replaces `web` and `worker`;
the worker has 30 s to hand its leases back (ADR 0005). Read the release notes for owner-facing
steps, and compare the release's swag sample with the installed one
(`git diff vOLD vNEW -- docs/runbooks/swag-classbeat.subdomain.conf`). A stack older than the
Swedebeat connection (#366) has no `secrets/swedebeat_app`, and one older than #507 has no
`secrets/oauth_*_client_secret`; without them `up` fails with a missing-file error for that secret,
which the `touch` line above fixes (an empty OAuth file keeps that provider off; `touch` never empties
a file). A file it creates gets group 10010 from the folder and mode 640 from `umask 027`. A stack
set up before #511 has files of mode 600 and the deploy user's group, which the containers cannot
read: run the one-time commands of section 5 (`groupadd`, `chgrp` and `chmod 2700` on the folder,
then `sudo chgrp 10010 secrets/* && chmod 640 secrets/*`) before `up`.

### Rollback

Migrations are not run backwards: the old image's `migrate` service does `alembic upgrade head`,
and a database stamped with a revision the old image does not know makes it fail ("Can't locate
revision"), so `db-functions`, `web` and `worker` never start. Setting `CLASSBEAT_VERSION` back is
therefore **only** safe when the release you are leaving added no migration (compare the revision
files between the two tags: `git diff --stat vOLD vNEW -- backend/migrations/versions`). Some
migrations also drop or reshape data, so treat none as reversible.

- **No migration in the release:** set `CLASSBEAT_VERSION` back to the previous tag,
  `git checkout vOLD`, `docker compose up -d --wait`.
- **A migration ran** (any kind): restore the pre-update backup with `scripts/restore.sh`
  ([backup-restore.md](backup-restore.md), "Restoring into a fresh stack": it verifies the signature,
  validates the archive, runs `migrate` and `db-functions` of the image you go back to, restores,
  and refuses a volume that still exists or a database that holds tables).
  ```bash
  docker compose down                                            # keeps volumes
  git checkout vOLD && sed -i 's/^CLASSBEAT_VERSION=.*/CLASSBEAT_VERSION=OLD/' .env
  docker volume rm "$(docker compose config --format json | jq -r .name)_db-data"
  scripts/restore.sh --expect-signer "$S_FPR" "$BACKUP_DIR/classbeat-<UTC timestamp>.dump.gpg"
  docker compose up -d --wait
  ```
  Anything written since the backup is lost; tell the testers. Deleting `db-data` here is the one
  deliberate exception to the rule below, and only once the backup is verified and off the volume
  (`scripts/restore.sh --verify-only ...` first).

Never delete `db-data` to "reset" a production stack.

### Key-compromise procedure (`maint`)

The dev `compose.yaml` has a `maint` service (ADR 0004 section 2, `keys wipe`) that
`compose.prod.yaml` does not. Adding it, with the owner role's secret and the SMTP settings, is a
follow-up; until then that procedure has no prepared command on the server.

## 12. Backups and the restore drill

Unchanged by the move to swag: [backup-restore.md](backup-restore.md) is the procedure
(`scripts/backup.sh`, `scripts/restore.sh`, `scripts/validate-backup.sh`, the drill
`scripts/backup-restore-drill.sh`, the recipient and signing keys, schedule and retention). The
scripts pick the stack like any compose command, so with `COMPOSE_FILE` in `.env` (section 5) they
run from `/opt/classbeat` as they are. Install the schedule there (cron or a systemd timer as the
deploy user), keep the copies off this machine, and run the drill once on your own data before the
testers arrive. The secret files are backed up separately (section 5).

## 13. What agents checked, and what they did not

`scripts/check-prod-compose.sh` (also a CI step) renders `compose.prod.yaml` alone and with the
loopback overlay using dummy values, checks the invariants (no Caddy and no published port, web on
`127.0.0.1` only with the overlay, one private trusted proxy equal to `PROXY_IP` or the `web-out`
gateway, the real `PROXY_IP` from the shell or `.env` when one is set, swag's address outside the
section 6a `--ip-range`, only web on the proxy network, internal `backend`, `db` unpublished, read-only web without
capabilities, healthchecks, no model key on web, the secret placement, log rotation), proves each
unsafe mutation of the rendered files is refused, and runs `nginx -t` on the swag sample with
stand-ins for swag's `ssl.conf` and the Cloudflare list, plus the lines the app relies on (exactly
one `X-Forwarded-For` line, the `$remote_addr` overwrite, no other include, the real-ip header, the
body cap, `server_tokens off`, the SSE location, no CSP or `Referrer-Policy`, no per-location
headers and no `proxy_intercept_errors`). For OAuth sign-in (#507) it checks that the three client
secrets are `secrets/` files mounted on web only, that no service has a variable named like a secret,
that the client ids and visible providers come only from their variables (empty and `google` without
them), that a canary in the dummy secret files never shows in the rendered files, and that `.env` holds
no `*CLIENT_SECRET*` variable. For the secret files (#511) it checks that every service that mounts a
secret has `group_add: ["10010"]` and runs as a non-root user (`postgres` for the postgres image,
whose gosu would drop the group), that each secret is the file `secrets/<its own name>` mounted at that
name, that no service bind-mounts `secrets/`, a folder above it or a file in it, and that no command,
entrypoint or healthcheck names `/run/secrets`; on the server, the mode and group of each real file.
That a uid 10001 container with group 10010 reads a `root:10010` mode 640 or 440 file and one without
the group cannot, and that gosu drops the group, was checked with `compose.prod.yaml` on Docker 29.9.2 /
Compose 5.6.1 (#511). That the Docker port relay presents the `web-out`
gateway (section 6b) was checked on a throwaway container on Docker 29.

Not checked: a running prod stack behind a real swag (the image, Cloudflare, the certificate, the
real mail path and the worker's start-up), which is the post-deploy checklist above.
