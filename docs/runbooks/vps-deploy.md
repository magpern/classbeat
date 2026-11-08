# Server deploy (M6): compose.prod.yaml behind swag

Status: **configuration ready, not deployed.** The server is the owner's own machine; DNS for
`classbeat.pernemark.se` is in Cloudflare and points at it; TLS and routing are the owner's
[swag](https://docs.linuxserver.io/general/swag/) (linuxserver/swag, nginx) container. Everything
here is the owner's step: deploying, DNS, TLS and the mail account are in the safety envelope's "only
with the owner's authorization" list (AGENTS.md). **Agents cannot reach the server.** The deploy is
run from a local session on that machine (the owner's terminal, or a Claude Code session the owner
starts there), one step at a time with the owner's go-ahead. Agents checked the files offline
(`check-prod-compose.sh`, section 13); they never touched the server, DNS, Cloudflare or a provider.

**Upgrade notes (read before `up`).**

- **From the release with #792 (Turnstile on by default in production, [ADR 0025](../adr/0025-turnstile.md)
  Amendment 2).** `compose.prod.yaml` now passes `CLASSBEAT_TURNSTILE_ENABLED=${TURNSTILE_ENABLED:-true}`,
  so a `.env` that does not mention `TURNSTILE_ENABLED` turns it on. A server **without a Turnstile
  site key** (`TURNSTILE_SITE_KEY` in `.env`) **or without the key in `secrets/turnstile_secret_key`**
  must put `TURNSTILE_ENABLED=false` in `.env` **before** the upgrade: `web` then fails its start-up
  settings check (`turnstile_enabled needs CLASSBEAT_TURNSTILE_SITE_KEY`, or `turnstile_enabled needs
  the turnstile_secret_key secret file (it is empty)`, from `config_turnstile.py`) and keeps
  restarting. Remove the `false` again once the widget and keys exist (section 5b; the full steps
  are in section 11).

What is deployed: `compose.prod.yaml` (web, worker, db, and the one-shot migrate and db-functions)
from the release images `ghcr.io/magpern/classbeat:X.Y.Z` and `ghcr.io/magpern/classbeat-db:X.Y.Z`
(Postgres with the role, job-queue and admin-role SQL baked in), plus one site file in swag
([swag-classbeat.subdomain.conf](swag-classbeat.subdomain.conf)). Caddy, the edge of the first
version of this runbook (#341), was removed in #492: swag already owns ports 80 and 443 on this
machine, and a second, unused edge config would only drift.

**Nothing is cloned.** The server needs Docker, the files of one GitHub Release (section 3), a
`.env`, a `secrets/` folder and a token to pull the private images (section 4): ADR 0020, #519.

```
visitor -> (Cloudflare, if proxied) -> :443 swag -(proxy, internal)-> web:8000 -(backend, internal)-> db
                                                   web -(web-out)-> SMTP, OAuth and Spotify token endpoints
                                                   worker -(backend)-> db ; worker -(egress)-> model providers, MOWL, Spotify
```

No classbeat service publishes a port. `proxy` is a Docker network the owner creates once and that
only swag and web join; swag has a fixed address on it (`PROXY_IP`). `backend` is internal: `db`,
`migrate` and `db-functions` have no route out and `db` has no port. There is no mailpit (mail goes
directly to a real SMTP service; Proton Mail Bridge is an alternative,
[proton-bridge-smtp.md](proton-bridge-smtp.md)).

## 1. Prerequisites

- Docker Engine with **Compose 2.24.4 or later** (`docker compose version`), a non-root user in the
  `docker` group for the deploy, SSH (if any) by key only, automatic security updates, time sync on.
- swag running and serving another site already (its certificate covers `classbeat.pernemark.se`:
  a wildcard, or the name in `SUBDOMAINS`/`EXTRA_DOMAINS`), its config volume at hand (`/config`).
- Disk for the Postgres volume and the json-file logs (at most 50 MB per container).
- A mail account for the app, reached directly over SMTP (the default: SMTP host, 587 with STARTTLS
  or 465 with TLS, a login, a sender on a domain you control, SPF and DKIM). Proton Mail Bridge is
  an alternative, not assumed anywhere below unless a step says so.
- The release you deploy exists, with its assets: <https://github.com/magpern/classbeat/releases> (tag
  `vX.Y.Z`; the owner or an agent checks `gh run list --workflow release.yml`). No `git`, no clone.

## 2. What is reachable from the internet

Only swag: the router forwards `443/tcp` (and `80/tcp` if swag answers HTTP challenges or redirects)
to swag and nothing else. Host and network hardening is the operator's choice; classbeat does not
require or configure any firewall. Ports Docker publishes bypass the host's `ufw`, so if you rely on
the router (or an upstream firewall), never forward 5432, 8000 or a classbeat port. With the
loopback overlay (section 6b) web listens on `127.0.0.1` only. `docker ps` must show no classbeat
container with a host mapping other than that (section 9).

## 3. Get the files

The server needs the files attached to the GitHub Release of the version you run. The repository is
public, so no login is needed to download them (the images are private, section 4). Once:

```bash
sudo mkdir -p /opt/classbeat && sudo chown "$USER" /opt/classbeat
cd /opt/classbeat
V=X.Y.Z                                           # the release, without the v
for f in compose.prod.yaml compose.prod.loopback.yaml compose.prod.lan.yaml check-prod-compose.sh \
  check-prod-compose-rollback.bash check-prod-compose-setup.bash check-prod-compose-rules.bash check-prod-compose-secrets.bash \
  check-prod-compose-selftest-compose.bash check-prod-compose-selftest-yaml.bash check-prod-compose-owner.bash check-prod-compose-secretfiles.bash \
  check-prod-compose-turnstile.bash check-prod-compose-runbook.bash check-prod-compose-site.bash check-prod-compose-site-selftest.bash check-prod-compose-probe.bash \
  swag-classbeat.subdomain.conf prod.env.example backup.sh restore.sh validate-backup.sh SHA256SUMS; do
  curl --proto '=https' --tlsv1.2 -fsSLO "https://github.com/magpern/classbeat/releases/download/v$V/$f"
done
sha256sum -c SHA256SUMS                           # every line OK
chmod +x check-prod-compose.sh backup.sh restore.sh validate-backup.sh   # a download has no exec bit
cp prod.env.example .env                          # filled in by section 5
```

(`gh release download "v$V" --repo magpern/classbeat` does the same.) The files are the release
tag's own repository files, byte for byte (only `SHA256SUMS` is generated at release time). They
hold no secret.

**What `sha256sum -c` proves, and what it does not.** `SHA256SUMS` comes from the same release over
the same connection as the files, so it catches a truncated or corrupted download and **nothing
else**; it is not protection against tampering.

**Verify the attestation (tamper check).** The release workflow attests every asset, `SHA256SUMS`
included (GitHub artifact attestation, signed by that workflow run). With the GitHub CLI (`gh`, a
trusted install of your own; `gh auth login` first if it asks, the repository is public), before
anything else runs:

```bash
gh attestation verify SHA256SUMS -R magpern/classbeat --signer-workflow magpern/classbeat/.github/workflows/release.yml || echo "NOT VERIFIED: SHA256SUMS"
for a in $(awk '{print $2}' SHA256SUMS); do        # the names in the verified list: the other twenty-one assets
  gh attestation verify "$a" -R magpern/classbeat --signer-workflow magpern/classbeat/.github/workflows/release.yml || echo "NOT VERIFIED: $a"
done
```

Each file must print a verified result; any `NOT VERIFIED` means stop and do not run it. What this
proves: the file is byte for byte what the release workflow of `magpern/classbeat` attested for a tag.
What it does not: `gh` itself and your network path to GitHub's attestation service are trusted, and a
verifier cannot vouch for itself, so `check-prod-compose.sh` does not verify its own attestation (it is
verified here, by `gh`, before it runs). Releases made before the attestation step existed (and
the first one after it, until the owner has seen it work) have no attestation: use `sha256sum -c`
and the trust below. **Unverified:** no release has run with the attestation step yet (agents cannot
push tags), so the exact output of these commands is not confirmed; the first release must be checked
by hand, and this section corrected if it differs.

Without `gh`, the trust is GitHub's TLS plus the people with write access to the repository and its
releases: no worse than the `git clone` of a tag this replaced, and no better.
Keep them next to
`.env` and `secrets/` in `/opt/classbeat`: the compose files use `./secrets/<name>`, and
`check-prod-compose.sh` finds `.env` and `secrets/` beside itself. `swag-classbeat.subdomain.conf`
goes to swag (section 7), not into the stack. A developer in a checkout of the repository finds the
same files under `compose.prod*.yaml`, `scripts/` and `docs/runbooks/`; nothing in this runbook needs one.

## 4. Pull the private images from GHCR

The packages `ghcr.io/magpern/classbeat` (the app) and `ghcr.io/magpern/classbeat-db` (Postgres with
the SQL baked in) are private (never change that). On the server, once:

1. On GitHub create a **personal access token (classic)** with only the `read:packages` scope,
   expiring in 90 days. A classic `read:packages` token reads **every package its account can
   see**, so create it on an account (or machine user) that is given read access to these two
   packages and little else; never your admin token. (That fine-grained tokens cannot read GHCR is
   unverified here; use one only if GitHub now offers a packages-read permission scoped to both packages.)
2. `echo "$TOKEN" | docker login ghcr.io -u <github-user> --password-stdin` (type the token
   into the shell without it ending up in history: `read -rs TOKEN` first, then `unset TOKEN`).
   Docker keeps it in `~/.docker/config.json` of the deploy user (mode 600).
3. `docker pull ghcr.io/magpern/classbeat:X.Y.Z` and `docker pull ghcr.io/magpern/classbeat-db:X.Y.Z`
   must both work. Put the token's expiry in your calendar: an expired token breaks the next
   update, not the running site.

Never use `latest` (there is none); `CLASSBEAT_VERSION` in `.env` pins a release, and both images
and the compose files come from that one release.

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

**The one step that needs `sudo` (first install only).** The group and the folder's group and
setgid bit must be set by root, because an owner outside the group cannot set them himself. Do it
before anything else in `secrets/`; `./check-prod-compose.sh` fails with the exact command if it was
skipped. The group name is only a label; the number is what counts, and it must not already belong to
something else:

```bash
getent group 10010 || sudo groupadd --system --gid 10010 classbeat-secrets   # must have no members
cd /opt/classbeat && mkdir -p secrets && sudo chgrp 10010 secrets && sudo chmod 2700 secrets   # sudo: an owner outside the group cannot set the group or g+s
```

Then as the deploy user (no sudo: the files get group 10010 from the folder, mode 640 from the umask):

```bash
cd /opt/classbeat
( umask 027                 # in a subshell: your shell keeps its own umask
  for n in postgres_password database_owner_password database_password database_maint_password; do
    head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n' > "secrets/$n"
  done
  head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n' > secrets/app_secret_key      # >= 32 bytes
  head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n' > secrets/connection_kek      # exactly 64 hex characters
  for n in smtp_password anthropic_api_key openai_api_key deepseek_api_key swedebeat_app \
    oauth_google_client_secret oauth_github_client_secret oauth_microsoft_client_secret \
    turnstile_secret_key; do
    [ -e "secrets/$n" ] || install -m 640 /dev/null "secrets/$n"   # empty placeholder; fill in below
  done )
ls -ln secrets/          # every file: <your uid> 10010, -rw-r-----
```

Files made before this change (or anywhere else and moved in) keep their old group and mode; fix
them once with `sudo chgrp 10010 secrets/* && chmod 640 secrets/*`. `./check-prod-compose.sh`
checks every file named in `compose.prod.yaml` on the server: a regular file (not a link), group
10010, the group may read and not write, nothing for others, and the folder group 10010 with the
setgid bit and mode 2700, nothing for the group or others (it prints names, modes and group numbers and the command that fixes
each, never a file's content; the sudo ones say so).

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
- `secrets/turnstile_secret_key`: the loop above makes it **empty**, which only works with
  `TURNSTILE_ENABLED=false` in `.env`. Turnstile is **on by default**: write the secret key into it
  (section 5b) or set `TURNSTILE_ENABLED=false` until the widget exists. It is mounted into `web` only, so the file
  must exist or `up` fails. Web refuses to start with Turnstile on and an empty file (zero bytes, or only blanks or a newline) or no
  `TURNSTILE_SITE_KEY`, and in prod with one of Cloudflare's test keys ([ADR 0025](../adr/0025-turnstile.md) section 7).

A file that holds only blanks counts as empty. If an optional file (`smtp_password`, the three
`oauth_*_client_secret`, `swedebeat_app`, `turnstile_secret_key`) cannot be read, `web` starts without it and logs
`secret file <name> is not readable; treated as not set` (that feature is off); any other unreadable
secret stops the service that needs it. Either way, fix the group and mode as above. For
`swedebeat_app` that means the runtime override silently does not apply: web falls back to the key
built into the release image, and only that warning says so.

**Back up `secrets/connection_kek` and `secrets/postgres_password` and the other secret files
off the server now** (a password manager or an encrypted offline copy). Losing `connection_kek`
makes every stored MOWL and Spotify connection unreadable (ADR 0004); the database backup is no
use without it.

`/opt/classbeat/.env` (not committed; section 3 copied `prod.env.example` to it, which has every
variable with a placeholder and a comment). Fill in at least the values below. `COMPOSE_FILE` makes
every plain `docker compose` command in this runbook, and `backup.sh` / `restore.sh`, use the
production files; nothing below needs `-f`.

```
COMPOSE_FILE=compose.prod.yaml
COMPOSE_PROJECT_NAME=classbeat-prod              # restore.sh refuses the default name `classbeat`
# alternative, Proton Mail Bridge instead of direct SMTP: COMPOSE_FILE=compose.prod.yaml:compose.bridge.yaml   (proton-bridge-smtp.md)
# with a proxy on the host: COMPOSE_FILE=compose.prod.yaml:compose.prod.loopback.yaml   (section 6b)
# with swag on another host: COMPOSE_FILE=compose.prod.yaml:compose.prod.lan.yaml   (section 6c)
# both bridge and an overlay: the overlay first, bridge last
CLASSBEAT_VERSION=X.Y.Z                          # the release whose files you downloaded
CLASSBEAT_DOMAIN=classbeat.pernemark.se          # web gets CLASSBEAT_PUBLIC_BASE_URL=https://classbeat.pernemark.se
PROXY_IP=172.29.90.10                            # swag's fixed address on the proxy network (section 6)
# optional: PROXY_NETWORK=classbeat-proxy        # the network's name, if you chose another
SMTP_HOST=smtp.example.test
SMTP_SENDER=noreply@classbeat.pernemark.se
# optional: SMTP_PORT=587  SMTP_TLS=starttls  SMTP_USERNAME=...  ADMIN_EMAILS=you@example.test
# only with Bridge: SMTP_CA_FILE=/run/smtp-ca/bridge.pem   (pinned self-signed SMTP certificate, see proton-bridge-smtp.md)
# optional: SIGNUP_MODE=invite  CONNECTION_KEK_ID=kek-1  MODEL_PROVIDER=anthropic
# optional: OAUTH_GOOGLE_CLIENT_ID=...apps.googleusercontent.com  OAUTH_VISIBLE_PROVIDERS=google   (section 5a)
# optional: OAUTH_GITHUB_CLIENT_ID=...  OAUTH_MICROSOFT_CLIENT_ID=...   (client ids only; never a client secret here)
# Turnstile is ON by default: set TURNSTILE_SITE_KEY=... and fill secrets/turnstile_secret_key (section 5b), or set TURNSTILE_ENABLED=false until they exist
# optional: PASSKEYS=true  WEBAUTHN_RP_ID=classbeat.pernemark.se   (passkeys.md; NOT CLASSBEAT_PASSKEYS: .env uses the unprefixed names)
```

.env uses the names **without** the `CLASSBEAT_` prefix (`PASSKEYS`, `WEBAUTHN_RP_ID`, `TURNSTILE_SITE_KEY`, ...):
`compose.prod.yaml` adds the prefix for the container, so a `CLASSBEAT_PASSKEYS` line in `.env` is silently ignored.
`check-prod-compose.sh` warns about such a key (its name only, never a value).

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
3. Put the client secret in its file, never in `.env` (`./check-prod-compose.sh` refuses a
   `*CLIENT_SECRET*` variable there). `secrets/` is git-ignored and never committed. Paste it without
   it reaching the shell history or the screen:
   ```bash
   cd /opt/classbeat
   ( umask 027; read -rs S && printf '%s' "$S" > secrets/oauth_google_client_secret; unset S )
   ls -ln secrets/oauth_google_client_secret          # <your uid> 10010, -rw-r----- like the others
   ```
4. Recreate web (only web reads these; the worker never gets them):
   ```bash
   ./check-prod-compose.sh
   docker compose up -d --wait web
   ```
5. Test while the consent screen is in **Testing** mode: only the test users listed there (Google
   Cloud Console, OAuth consent screen, Test users) can sign in. Open the site in a private window:
   *Continue with Google* is shown on the sign-in and sign-up screens; a test user signs in (a new
   account still needs an invite while `SIGNUP_MODE=invite`). Publishing the app to every Google
   account later needs a verified domain and public privacy policy and terms pages on it (Google's
   verification). The site serves them at `/en/privacy` and `/en/terms` (also `/sv/...` and `/da/...`;
   [ADR 0027](../adr/0027-language-prefixed-urls.md)). In the consent screen's app domain fields enter
   `https://<host>/en/privacy` and `https://<host>/en/terms`, not the bare `/privacy` and `/terms`: the
   bare addresses only redirect in the browser, and Google's verifier may not run JavaScript. This is a
   separate owner step.

**Provider off** (no client id, or an empty secret file): no Google button, `GET /api/auth/oauth/providers`
lists no `google`, and starting a Google sign-in answers 404 `provider_disabled`; email and password
sign-in work as before. To switch Google off again, empty the file (`: > secrets/oauth_google_client_secret`)
or remove the client id, then `docker compose up -d --wait web`. GitHub and Microsoft work the same way
with `OAUTH_GITHUB_CLIENT_ID` / `OAUTH_MICROSOFT_CLIENT_ID` and their secret files (redirect URI
`https://<host>/oauth/github/callback` or `/oauth/microsoft/callback`; Microsoft also needs the
`auth_time` optional claim, AGENTS.md), and appear on the screens only when named in
`OAUTH_VISIBLE_PROVIDERS`.

### 5b. Cloudflare Turnstile on the sign-in, sign-up, provider-email and reset forms (on by default)

[ADR 0025](../adr/0025-turnstile.md). Turnstile is **on by default** since the release that carries
Amendment 2 (`TURNSTILE_ENABLED` unset means `true`), so a server needs the widget, the site key and the
secret file before `web` starts, or `TURNSTILE_ENABLED=false` in `.env` until it has them (this
section is no longer optional). Everything below is the owner's; agents do not touch the Cloudflare dashboard.

1. **Widget.** Cloudflare dashboard, Turnstile, add a widget for the host name of the real site,
   exactly the value of `CLASSBEAT_DOMAIN` (today `classbeat.pernemark.se`); no `localhost`. Only
   the `CLASSBEAT_DOMAIN` host passes the check, so a second host on the widget is of no use.
   Mode **Managed**, pre-clearance **off**. The widget covers
   subdomains of a listed host, so `pernemark.se` as a hostname would also cover this one; list the
   exact host anyway. **When the domain changes, add the new host to the widget first**, then change
   `CLASSBEAT_DOMAIN`, then remove the old host. Two different symptoms: a host **missing from the
   widget** fails inside the widget (Cloudflare error `110200`; the page reports that the check
   failed or did not load, and no token is sent); "Security check did not pass" (400) means a token
   whose `hostname` is not the host of `CLASSBEAT_PUBLIC_BASE_URL`.
2. **Keys.** The site key is public and goes in `.env`: `TURNSTILE_SITE_KEY=0x4AAAAAAF3ZKJe4og1j7PsA`
   (the owner's key for this widget). The secret key goes in `secrets/turnstile_secret_key`, owner
   the deploy user, group 10010, mode 0640, and never in `.env`, a command argument or chat:
   `( umask 027; read -rs S && printf '%s' "$S" > secrets/turnstile_secret_key; unset S )`, then
   `ls -ln secrets/turnstile_secret_key` (mode `-rw-r-----`, group 10010). Rotating only the secret
   later needs `docker compose up -d --force-recreate web`: a changed file alone does not recreate
   the container. Never use Cloudflare's dummy test keys in production: web refuses to start.
3. **Enable.** A release that carries the default-on change needs nothing more once steps 1 and 2
   are done: `docker compose up -d web`. Web refuses to start when the site key is missing or the
   secret file is empty (the app also treats a file of only blanks or a newline as empty).
   `./check-prod-compose.sh` (section 9) refuses an empty (zero-byte) file before `up`
   (size test only, the file is never read, so a blank-only file passes it: write the key with the
   command in step 2). It renders with the caller's environment ahead of `.env`, so an exported
   `TURNSTILE_ENABLED` or `TURNSTILE_SITE_KEY` in your shell wins over `.env` there. Both refusals are skipped
   with `TURNSTILE_ENABLED=false`. A release older than that one has the widget (PR #731) but a default of
   off: set `TURNSTILE_ENABLED=true` in `.env` there. A release older than #731 has the backend check
   but no widget, so enabling it makes every sign-in, sign-up, provider-email and reset request fail
   with `captcha_failed`. With `TURNSTILE_ENABLED=false` web logs an `ERROR` at start and a `WARNING`
   for every protected request; that is expected only until this step.
4. **Live check** (report only pass or fail and the `why` word): sign in, sign up with an invite,
   request a password reset and finish a provider-email step; repeat in Swedish and Danish and in
   dark and light; the browser console shows no CSP violation and no error `110200` (domain or
   referrer); `docker compose logs web | grep 'captcha refused'` shows no line for those attempts.
   If anything fails, set `TURNSTILE_ENABLED=false` (step 6) and report it.
5. **Keep it on.** Nothing else is needed; an old `TURNSTILE_ENABLED=true` in `.env` may stay or
   go. Never leave `TURNSTILE_ENABLED=false` in `.env` once the keys exist.
6. **Emergency switch** (Cloudflare unreachable, or the check blocks real users): set
   `TURNSTILE_ENABLED=false` in `.env` and `docker compose up -d web`. Web then logs an `ERROR` at
   start and a `WARNING` on every protected request; every other limit still runs. Switch it back on
   once Cloudflare has recovered. There is no automatic bypass: while it is on and Cloudflare is
   unreachable, new sign-ins, sign-ups and reset requests answer 503; signed-in users are not affected.

The widget loads from `challenges.cloudflare.com`; if swag or Cloudflare rewrites the
`Content-Security-Policy` header, the check does not load (section 8). The privacy notice and the
cookie notice already name the check; they ship in the release, so do not run an older one with it on.

### 5c. CPU and memory ceilings (ADR 0026 stage 1)

`compose.prod.yaml` gives `web` and `worker` a CPU and a memory ceiling, so a Swedebeat mix analysis
(a child process of up to 1.5 GiB that keeps a core or more busy for up to 180 s) cannot starve the web
tier or the database on the one box. `db` has none: it is the one service that must never be killed
for memory. The defaults, and the `.env` variables that change them:

| Service | CPUs | Memory | Why |
|---|---|---|---|
| `worker` | `WORKER_CPUS=1.0` | `WORKER_MEMORY=3g` | `CLASSBEAT_MIX_ANALYSIS_CONCURRENCY` (default 1) x 1.5 GiB for the analysis child, plus about 1 GiB for the worker's own process and its other running job |
| `web` | `WEB_CPUS=1.0` | `WEB_MEMORY=1g` | one Python process: it cannot use much more than a core; the memory covers its pool and requests (bodies are capped at 64 KiB) |

- **Never set a CPU value above the host's core count** (`nproc`): Docker refuses to start the
  container. On a 2-core server keep the defaults (the worker gets at most one core, web and db share
  the rest); on 4 cores or more `WORKER_CPUS=2` lets one analysis and a design job run side by side.
- **Raise `WORKER_MEMORY` with the cap.** `CLASSBEAT_MIX_ANALYSIS_CONCURRENCY=2` needs `WORKER_MEMORY=4g`.
  The sum of `WORKER_MEMORY`, `WEB_MEMORY` and what Postgres uses must fit the server's RAM; with two
  worker containers (`--scale worker=2`, ADR 0026 stage 2) the worker's ceiling counts twice.
- **What happens at the ceiling.** CPU: the worker is slowed, never killed (the analysis has 180 s of
  wall clock; a slower decode can end `analysis_failed` and is retried by the user). Memory: the kernel
  kills the container (`docker inspect --format '{{.State.OOMKilled}}' <container>` says `true`);
  `restart: unless-stopped` starts it again, and the reaper requeues its jobs when their leases run out.
  Repeated kills mean the ceiling is too low for the cap: raise `WORKER_MEMORY` or lower the cap.
- **Swap.** The file sets `mem_limit` only, not `memswap_limit` (the production check refuses any
  key it does not list). On a host with swap enabled, a container that reaches its ceiling can
  therefore swap before the kernel kills it, so it gets slow instead of restarting. Check with
  `free -h`; on a small server prefer no swap, or size `WORKER_MEMORY` knowing it is not a hard cap.
- `scripts/check-prod-compose.sh` refuses a production file without both ceilings on `web` and `worker`.
- To see whether the ceilings bite: `docker stats --no-stream`, the `swedebeat mix analysed` log lines
  (`fetch_ms`, `decode_ms`, `peak_rss_mb`, `load_1m`) and `docker compose exec web python -m classbeat
  jobs stats` (queue wait and analysis p95; docs/developer-commands.md, "Measuring analysis load").

## 6. The proxy network and the client address

The app's per-IP rate limits (sign-in, sign-up, mail, MOWL connect), its auth events and the body
cap's log use the client address (ADR 0003 section 9): the TCP peer, unless the peer is in
`CLASSBEAT_TRUSTED_PROXIES`; then the right-most `X-Forwarded-For` entry that is not a trusted
proxy. **The decision (#492):**

- web trusts **exactly one** peer, swag, at a fixed private address (`PROXY_IP`). Not a range: any
  other container on that network, the host, and Cloudflare are not trusted. In prod, web refuses
  to start unless the trusted proxy is one private address (a range, a list, a public address or a
  name is refused). `./check-prod-compose.sh` refuses the same in its dummy rendering and, when
  `PROXY_IP` is set in the shell or in `.env`, checks that real value too (one
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

### 6c. Alternative: swag on another machine (home or LAN)

For a swag (or any nginx) that runs on a different machine of the same private network: add
`compose.prod.lan.yaml` to `COMPOSE_FILE` (section 5). web joins no Docker network with the proxy.
It is published on this server's LAN address only, `WEB_LAN_ADDRESS:WEB_HOST_PORT` (default 8080),
and `PROXY_IP` is **swag's LAN address**, the one peer whose `X-Forwarded-For` web believes (a
published port keeps the sender's address, so web sees swag itself; confirm it with the checklist's
413 / logged-address test, as 6b was checked). **`PROXY_IP` must be an address swag has of its own on
the LAN** (swag on the host network, or a macvlan address). If swag runs in a bridge container on
another host Y, it reaches web from Y's address and every process and container on Y becomes a
trusted proxy that can name any client address: give swag its own address, or accept that. In `.env`:

```
COMPOSE_FILE=compose.prod.yaml:compose.prod.lan.yaml
WEB_LAN_ADDRESS=192.168.1.20        # this server, on the LAN; one private IPv4, never 0.0.0.0
```

and change `PROXY_IP` in the same file to the LAN address of the machine swag runs on
(for example `192.168.1.10`; it replaces the Docker-network address of section 6a).

In the swag sample set `set $upstream_classbeat 192.168.1.20;` and port `8080` in both `proxy_pass`
lines. **Restricting who can reach that port is the operator's choice; classbeat does not require
it and never changes the host's firewall.** Without a restriction, any machine on the LAN can reach
web under its own address. If you want one, an optional example: Docker's published ports bypass
`ufw` and `firewalld`, so the rule goes in the `DOCKER-USER` chain (it sees the packet after the port
translation; match the original destination, this server's address **and** the port):

```bash
sudo iptables -I DOCKER-USER -p tcp -m conntrack --ctorigdst 192.168.1.20 --ctorigdstport 8080 \
  --ctdir ORIGINAL ! -s 192.168.1.10 -j DROP
sudo iptables -L DOCKER-USER -n -v        # the rule is first; make it persistent with your distribution's tool
```

(with nftables-based Docker, the equivalent rule in the `DOCKER-USER` chain of the `filter` table).
If you added the rule, check from a third machine on the LAN: `nc -vz 192.168.1.20 8080` must
time out, and from the swag machine it must connect. `./check-prod-compose.sh` refuses a published address that is not
loopback or one private IPv4, more than one published port of web (both overlays together, or a
second port from an overlay of your own) and any port of another service, and a `PROXY_IP` equal to
the server's own address. It does not look at any firewall. The hop from swag to web is
**plain HTTP across the LAN** (cookies and forwarded headers in clear): acceptable on a home network you control, not on a shared one. Everything else
(sections 7 to 10) is unchanged.

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
5. Check the file as installed, not only the sample: section 9 runs `check-prod-compose.sh` with
   `PROD_CHECK_SWAG_CONF` set to the path of the copy in swag's volume (for example
   `/opt/swag/config/nginx/proxy-confs/classbeat.subdomain.conf`; copy it somewhere you can read if
   the volume is root-only). It reads the file as nginx does, directive by directive (several on a
   line, one split over lines, `#` and `;` inside quotes, one-line `location` blocks), and refuses a
   second `proxy_set_header X-Forwarded-For`, one that is not `$remote_addr`, one outside the
   server block, any `proxy_set_header` or `add_header` in a location (it would replace every
   inherited one), a second server block and the other lines the app relies on, then runs `nginx -t`
   on it.
6. **Access-log retention: 30 days (required; the privacy notice at `/privacy` says so).** The log
   holds each visitor's IP address, and the sample sets no retention itself. Write this rotation rule
   to `/config/log/classbeat-logrotate.conf` in swag's volume (the current file holds at most one
   day, so 29 rotated files make 30 days at most):

   ```
   /config/log/nginx/classbeat.access.log {
       daily
       rotate 29
       maxage 30
       missingok
       notifempty
       compress
       delaycompress
       copytruncate
   }
   ```

   Check it with `docker exec swag logrotate -d /config/log/classbeat-logrotate.conf`, and run it
   daily as the deploy user (cron or a systemd timer):
   `docker exec swag logrotate -s /config/log/classbeat-logrotate.status /config/log/classbeat-logrotate.conf`.
   If you change the period, change the privacy notice in the same PR (`privacy.retention.li7`).
   Likewise the security log's period is `CLASSBEAT_SECURITY_LOG_DAYS` (default 7, 1 to 30): if you
   change it, change the notice (`privacy.retention.li4`) too.

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
  - Turnstile (section 5b) is a separate Cloudflare product from the proxy: it works the same in
    DNS-only and proxied mode. The widget must list the site's host (and the new host first when the
    domain changes). Do not block or rewrite `https://challenges.cloudflare.com` in a rule, and keep
    Rocket Loader and the other injectors off, or the check does not load.
  - Optional hardening: allow 443 at the router/firewall only from Cloudflare's ranges, or turn on
    Authenticated Origin Pulls and verify Cloudflare's client certificate in the sample
    (`ssl_client_certificate` + `ssl_verify_client on`), so the origin cannot be reached around
    Cloudflare. Not needed for the client address (section 6), useful against direct floods.

## 9. First deploy

```bash
cd /opt/classbeat
sha256sum -c SHA256SUMS --quiet     # the downloaded files are unchanged (section 3)
./check-prod-compose.sh             # needs jq and docker compose; .env, PROXY_IP, secrets/ group 10010 mode 640
PROD_CHECK_SWAG_CONF=/opt/swag/config/nginx/proxy-confs/classbeat.subdomain.conf ./check-prod-compose.sh
                                    # again, with your swag file (section 7, step 5); the path is yours
docker compose pull                 # both images; needs the token of section 4
# the version as .env holds it, with any quotes, blanks or carriage return dropped (CLASSBEAT_VERSION="1.2.3" is valid)
V="$(sed -n 's/^CLASSBEAT_VERSION=//p' .env | tr -d "\"' \r")"
{ date -u +%FT%TZ; docker image inspect --format '{{index .RepoDigests 0}}' \
  "ghcr.io/magpern/classbeat:$V" "ghcr.io/magpern/classbeat-db:$V"; } >> deployed-images.txt   # the digests you run
./check-prod-compose.sh --probe     # every service reads every secret it mounts
docker compose up -d --wait
docker compose ps
```

Tags are mutable, so `deployed-images.txt` records the digest each pull resolved to; compare it
with the digests in the release notes (the release records each image's digest there) and with a later
pull of the same version.

**Verify the images before `up`** (needs `gh` and the pulled images, section 4):

```bash
gh attestation verify "oci://ghcr.io/magpern/classbeat:$V" -R magpern/classbeat --signer-workflow magpern/classbeat/.github/workflows/release.yml
gh attestation verify "oci://ghcr.io/magpern/classbeat-db:$V" -R magpern/classbeat --signer-workflow magpern/classbeat/.github/workflows/release.yml
```

Both must verify, and the digest they report must equal the one in `deployed-images.txt` and the
release notes. The release workflow attests each image's digest as GitHub artifact attestations (the
`provenance: mode=max` of the build is buildx's own registry attestation, a different thing). If
either fails, do not `up`. **Unverified:** like the assets, this has not run against a real release
yet; whether `gh` needs the `docker login` of section 4 to resolve the private image is also
unconfirmed.

The first check lints the files you downloaded with dummy values (a local edit that publishes a
port or adds a mount is refused), renders your real `.env` and checks the real `PROXY_IP`, where web is
published and the real secret files, with the fix for each failure (the group and the folder's
setgid bit need `sudo` once, section 5). `--probe` runs, for each service that mounts a secret, the
pulled image with a shell that asks the kernel whether each file in `/run/secrets` is readable: one
line `<service>: <file> OK` or `... FAIL` per file, never a value; any `FAIL` fails the check, and
you fix the group and mode (section 5) before `up`.

Needs of the check: the `docker compose` plugin (it renders your `.env`); `jq`; and, only for
`PROD_CHECK_SWAG_CONF`, a running Docker daemon and the image `nginx:1.28.0`, which the check pulls
from Docker Hub on its first run (`PROD_CHECK_NGINX_IMAGE` names another; the run needs the network
once, then it is local). The check reads your `.env` and `secrets/` metadata and sends nothing out
but that pull; it prints no value.

When the swag container is running here (named `swag`, or `PROD_CHECK_SWAG_CONTAINER`) and the
shared network is in use, the check also compares `PROXY_IP` with the address Docker gave swag and
fails on a difference: a typo such as `.130` for `.10` lies inside Docker's allocation range, so the
form check alone passes it, and web would then trust nobody and see every visitor as swag. When it
cannot see swag (no daemon, another host, the loopback or LAN overlay, another container name) it
says so and does not fail; then compare by hand:

```bash
docker inspect --format '{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}' swag
grep '^PROXY_IP=' .env
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
- [ ] `./check-prod-compose.sh --probe` (section 9) printed `OK` for every file and no `FAIL`;
      `ls -ln secrets/` shows group `10010` and `-rw-r-----` (or stricter) on every file, and
      `./check-prod-compose.sh --probe` passes on the server.
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
- [ ] The access-log rotation of section 7 step 6 is installed and scheduled (30 days at most), so the privacy notice is true.
- [ ] Run an external header/TLS check of the domain (for example SSL Labs, securityheaders.com).
- [ ] Language-prefixed pages (ADR 0027): `curl -s https://<host>/sv/privacy | grep -E 'lang="sv"|canonical|hreflang'`
      shows the language, one canonical link on `/sv/privacy` and four alternates (en, sv, da, x-default);
      `curl -s https://<host>/sitemap.xml` and `/robots.txt` answer 200; `curl -sI https://<host>/fr/privacy`
      is `404`. No DNS, TLS, Cloudflare or swag change is needed for the prefix (swag sends every path but the SSE one to web).
- [ ] The owner submits `https://<host>/sitemap.xml` in Google Search Console and Bing Webmaster Tools
      (and enters the `/en/privacy` and `/en/terms` URLs in the Google consent screen, section 5a).
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

**Take a backup first** (section 12), always: it is the rollback. An update replaces the release's
files and the version; nothing is cloned or checked out.

**Upgrading to the release that turns Turnstile on by default ([ADR 0025](../adr/0025-turnstile.md)
Amendment 2): before `up`, either have the Cloudflare widget, `TURNSTILE_SITE_KEY` in `.env` and the
key in `secrets/turnstile_secret_key` (section 5b), or put `TURNSTILE_ENABLED=false` in `.env`.
Otherwise `web` refuses to start (the `touch` line below only creates an empty secret file).** Remove
the `false` again once the widget and keys exist.
The `./check-prod-compose.sh` run below fails with section 5b's pointer when Turnstile is on and the
site key or the secret file is empty, so a forgotten step shows up before `up`.

```bash
cd /opt/classbeat
OLD="$(sed -n 's/^CLASSBEAT_VERSION=//p' .env | tr -d "\"' \r")"; NEW=X.Y.Z   # NEW = the release you want, no v
echo "$OLD"                                       # write it down: a new shell has no $OLD (Rollback, below)
# 1. Download into a staging folder and verify there: a failed download changes nothing that runs.
rm -rf "staging-$NEW" && mkdir "staging-$NEW"
for f in compose.prod.yaml compose.prod.loopback.yaml compose.prod.lan.yaml check-prod-compose.sh \
  check-prod-compose-rollback.bash check-prod-compose-setup.bash check-prod-compose-rules.bash check-prod-compose-secrets.bash \
  check-prod-compose-selftest-compose.bash check-prod-compose-selftest-yaml.bash check-prod-compose-owner.bash check-prod-compose-secretfiles.bash \
  check-prod-compose-turnstile.bash check-prod-compose-runbook.bash check-prod-compose-site.bash check-prod-compose-site-selftest.bash check-prod-compose-probe.bash \
  swag-classbeat.subdomain.conf prod.env.example backup.sh restore.sh validate-backup.sh SHA256SUMS; do
  curl --proto '=https' --tlsv1.2 -fsSLo "staging-$NEW/$f" "https://github.com/magpern/classbeat/releases/download/v$NEW/$f"
done
( cd "staging-$NEW" && sha256sum -c SHA256SUMS --quiet )          # truncation only, see section 3
# The tamper check of section 3 (gh attestation verify, per file) also runs in staging-$NEW before step 2.
# 2. Keep the files you run now (the rollback files), then replace them.
mkdir -p "previous-$OLD" && cp compose.prod*.yaml check-prod-compose*.sh check-prod-compose-*.bash backup.sh restore.sh validate-backup.sh \
  swag-classbeat.subdomain.conf prod.env.example SHA256SUMS "previous-$OLD/"
mv -f "staging-$NEW"/* . && rmdir "staging-$NEW"
chmod +x check-prod-compose.sh backup.sh restore.sh validate-backup.sh   # a download has no exec bit
diff -u previous-$OLD/swag-classbeat.subdomain.conf swag-classbeat.subdomain.conf   # swag's copy changed?
diff -u previous-$OLD/prod.env.example prod.env.example                              # new settings to put in .env?
( umask 027; touch secrets/swedebeat_app secrets/turnstile_secret_key \
  secrets/oauth_google_client_secret secrets/oauth_github_client_secret secrets/oauth_microsoft_client_secret )
sed -i "s/^CLASSBEAT_VERSION=.*/CLASSBEAT_VERSION=$NEW/" .env
./check-prod-compose.sh                           # files, .env, secrets/: group 10010, mode 640 or stricter
docker compose pull                               # then append the new digests to deployed-images.txt (section 9)
./check-prod-compose.sh --probe
docker compose up -d --wait
```

Your own `compose.bridge.yaml` (`proton-bridge-smtp.md`) is not a release asset and is never touched.
`db` is **recreated on every update** (its image reference changes with `CLASSBEAT_VERSION`), so
Postgres restarts for a few seconds; the data volume is kept.

`up` re-runs `migrate` (idempotent) and `db-functions` before it replaces `web` and `worker`;
the worker has 30 s to hand its leases back (ADR 0005). Read the release notes for owner-facing
steps, and copy the swag sample to swag if the first `diff` shows a change. A stack older than the
Swedebeat connection (#366) has no `secrets/swedebeat_app`, and one older than #507 has no
`secrets/oauth_*_client_secret`, and one older than #724 has no `secrets/turnstile_secret_key`;
without them `up` fails with a missing-file error for that secret, which the `touch` line above fixes
(an empty OAuth file keeps that provider off; an empty Turnstile file only works with
`TURNSTILE_ENABLED=false`, because Turnstile is on by default; `touch` never empties a file). A file it
creates gets group 10010 from the folder and mode 640 from `umask 027`. A stack
set up before #511 has files of mode 600 and the deploy user's group, which the containers cannot
read: run the one-time commands of section 5 (`groupadd`, `chgrp` and `chmod 2700` on the folder,
then `sudo chgrp 10010 secrets/* && chmod 640 secrets/*`) before `up`; the check names them.

### Rollback

Migrations are not run backwards: the old image's `migrate` service does `alembic upgrade head`,
and a database stamped with a revision the old image does not know makes it fail ("Can't locate
revision"), so `db-functions`, `web` and `worker` never start. Setting `CLASSBEAT_VERSION` back is
therefore **only** safe when the release you are leaving ran no migration the old one lacks. Test it
before you touch anything; it needs no clone. With the stack still running on the new release:

```bash
cd /opt/classbeat
OLD=X.Y.Z      # the version you were running (section 11 printed it); a new shell does not have $OLD, set it again
./check-prod-compose.sh --rollback-check "$OLD"     # OLD = the version you would go back to
```

It reads the database's Alembic revision (`alembic_version`) and the migration files in
`ghcr.io/magpern/classbeat:$OLD` (a revision's id is its file name; log in and pull first if the
image is gone), and answers either "every database revision is known to release OLD" (exit 0) or
names the revision OLD lacks and tells you to restore the backup (exit 1). Some migrations also drop
or reshape data, so even a pass is a rollback of the software, not of data written since.

- **The check passes (no migration):** restore the previous release's files (`cp previous-$OLD/* .`;
  they are the ones you downloaded for it), set `CLASSBEAT_VERSION=$OLD` in `.env`,
  `./check-prod-compose.sh && docker compose pull && docker compose up -d --wait`. If the
  `previous-$OLD` folder is gone, download that release's assets again as in section 3. The first
  release that carries assets has no earlier release with assets: going back below it needs that
  tag's checkout once (`git clone --depth 1 --branch vOLD`, files only).
- **The check fails (a migration ran), or you cannot run it:** restore the pre-update backup with `restore.sh`
  ([backup-restore.md](backup-restore.md), "Restoring into a fresh stack": it verifies the signature,
  validates the archive, runs `migrate` and `db-functions` of the image you go back to, restores,
  and refuses a volume that still exists or a database that holds tables).
  ```bash
  docker compose down                                            # keeps volumes
  cp previous-$OLD/* . && sed -i "s/^CLASSBEAT_VERSION=.*/CLASSBEAT_VERSION=$OLD/" .env
  docker volume rm "$(docker compose config --format json | jq -r .name)_db-data"
  ./restore.sh --expect-signer "$S_FPR" "$BACKUP_DIR/classbeat-<UTC timestamp>.dump.gpg"
  docker compose up -d --wait
  ```
  Anything written since the backup is lost; tell the testers. Deleting `db-data` here is the one
  deliberate exception to the rule below, and only once the backup is verified and off the volume
  (`./restore.sh --verify-only ...` first).

Never delete `db-data` to "reset" a production stack.

### Key-compromise procedure (`maint`)

The dev `compose.yaml` has a `maint` service (ADR 0004 section 2, `keys wipe`) that
`compose.prod.yaml` does not. Adding it, with the owner role's secret and the SMTP settings, is a
follow-up; until then that procedure has no prepared command on the server.

## 12. Backups and the restore drill

Unchanged by the move to swag and by the image-only install: [backup-restore.md](backup-restore.md) is
the procedure. `backup.sh`, `restore.sh` and `validate-backup.sh` are release assets (section 3) and
run from `/opt/classbeat`, where `COMPOSE_FILE` in `.env` (section 5) picks the production stack; write
`./backup.sh` where that runbook says `scripts/backup.sh`. The restore drill script
(`scripts/backup-restore-drill.sh`) and the recipient and signing keys are covered there; the drill
is a developer tool that needs a checkout (run it on any machine against your own backup, or copy the
file from the release tag). Install the schedule (cron or a systemd timer as the deploy user), keep
the copies off this machine, and run the drill once on your own data before the testers arrive. The
secret files are backed up separately (section 5).

## 13. What agents checked, and what they did not

`scripts/check-prod-compose.sh` (also a CI step, and the release asset `check-prod-compose.sh` you run
in section 9; it sources the `check-prod-compose-*.bash` assets beside it, which section 3 downloads with it
and which it refuses to run without) renders `compose.prod.yaml` alone and with the loopback and LAN overlays
using dummy values, checks the invariants (no Caddy and no published port, web on
`127.0.0.1` only with the overlay, one private trusted proxy equal to `PROXY_IP` or the `web-out`
gateway, the real `PROXY_IP` from the shell or `.env` when one is set, swag's address outside the
section 6a `--ip-range`, only web on the proxy network, internal `backend`, `db` unpublished, read-only web without
capabilities, healthchecks, no model key on web, the secret placement, log rotation), proves each
unsafe mutation of the rendered files is refused, and runs `nginx -t` on the swag sample with
stand-ins for swag's `ssl.conf` and the Cloudflare list, plus the directives the app relies on
(#500: the file is read as nginx reads it, not line by line, so a second directive on one line, split
over lines, behind a quoted `#` or inside a one-line `location` is seen; exactly one
`proxy_set_header X-Forwarded-For`, in the server, with `$remote_addr`; no other include, the
real-ip header, the body cap, `server_tokens off`, one server block, the SSE location, no CSP or
`Referrer-Policy`, no `proxy_set_header` or `add_header` in any location and no
`proxy_intercept_errors`), and each spelling is a self-test case that is mutation-checked. With
`PROD_CHECK_SWAG_CONF` it applies the same rules and `nginx -t` to your own swag file. Like the
compose rules it is a lint of honest mistakes: it cannot know what else swag's own configuration
adds around the file, so the post-deploy checklist's client-address test is still the proof. For OAuth sign-in (#507) it checks that the three client
secrets are `secrets/` files mounted on web only, that no service has a variable named like a secret,
that the client ids and visible providers come only from their variables (empty and `google` without
them), that a canary in the dummy secret files never shows in the rendered files, and that `.env` holds
no `*CLIENT_SECRET*` variable. For the secret files (#511) the compose checks are a **best-effort lint
against honest mistakes** in the owner's own committed, reviewed compose files. They are **not a security
boundary** against someone who can edit those files (such a person could run anything). The boundary
for the secrets is on the server: each file's mode and group (`<deploy user>:10010`, 640, in a 2700
folder; section 5) and which services mount which secret. The script is **default-deny** (#517). It renders every variant (`compose.prod.yaml` alone and with
the loopback and LAN overlays) with all profiles (`--profile '*'`), and every key of the rendered
file must be on an allow-list of exactly what those files use today: at the top level (`name`,
`services`, `networks`, `volumes`, `secrets`, `x-*`), per service **by name** (a new service is
refused) and in each nested block (secret mounts, volume entries, `healthcheck`, `logging` and its
options, `depends_on`, service `networks`, `ports`, and the top-level networks with `ipam`, volumes and
secrets). Any other key is refused by its name: `configs`, `post_start`/`pre_stop`, `profiles`, `pid`,
`ipc`, `privileged`, `cap_add`, `devices`, `volumes_from`, `userns_mode`, `cgroup`, `cgroup_parent`,
`uts`, `runtime`, `gpus`, `provider`, `network_mode`, a driver option or a bind on a volume, and a key
Compose adds later. **Adding a compose key or a service to the production files therefore needs a
change to the allow-list in the script** (`check_keys`), reviewed with it. Each service's variables
are allowed **by name** too (#610): Compose drops `env_file` from the render but copies the file into
`environment`, however the key is spelled (`"env_file"`, `? env_file`, a flow mapping), so a variable the
files do not set is refused, and its name is never printed (an env file of a secret file puts the secret
in the name or the value). The top-level networks are allowed by key (`backend`, `egress`, `web-out`, the
project's own, and the external `proxy`). `env_file`, `label_file`, `profiles`, `extends` and `include`
are also refused in the files' text, as a fast lint only (a quoted or explicit key gets past a line
match; the render is the check). In section 9 the script also renders **your** configuration (`.env`,
the files `COMPOSE_FILE` names, every profile) and runs it through the same allow-list and secret-mount
rules, printing only key names and rule messages; the one addition of your own it accepts is
`compose.bridge.yaml` exactly as [proton-bridge-smtp.md](proton-bridge-smtp.md) writes it (the read-only
`./smtp-ca/bridge.pem` at `/run/smtp-ca/bridge.pem` and the external network `bridge-mail`, any `name:`,
joined by web alone). On the allowed keys it checks the values:
- every service that mounts a secret has `group_add: ["10010"]` and no `group_add` has another group;
  no service runs as root (uid or gid 0 or `root`, however written: `00`, `+0`, `postgres:0`), and a
  postgres service that mounts a secret runs as `postgres` (whose gosu would drop the group);
- `security_opt` is exactly `["no-new-privileges:true"]` (no seccomp, apparmor, systempaths or label
  option) and `cap_drop` exactly `["ALL"]` where present;
- each secret is the file `secrets/<its own name>`, mounted at that name;
- no bind mounts at all, only the named volume and tmpfs (the SQL is in the `classbeat-db` image, so no
  `secrets/`, `/proc`, Docker socket or other host path); `db` and `db-functions` run that image;
- the one named volume has its name only; the commands, entrypoints and tmpfs mounts are the ones in
  the files; every service logs with `json-file`;
- no command, entrypoint or healthcheck names `/run/secrets` (a text match).

Each of these, and each bypass the #512 reviews found (`volumes_from`, a volume's `driver_opts`, shared
pid or ipc, binds of `/proc`, `/proc/1/root` and the Docker socket, `configs`, hooks, a profiled
service, root as `00`), plus a key and a service nobody has heard of, is a self-test on every variant.
On the server, the script also checks the mode and group of each real file and that the `secrets/`
folder gives the group and others nothing (mode 2700).
That a uid 10001 container with group 10010 reads a `root:10010` mode 640 or 440 file and one without
the group cannot, and that gosu drops the group, was checked with `compose.prod.yaml` on Docker 29.9.2 /
Compose 5.6.1 (#511). That the Docker port relay presents the `web-out`
gateway (section 6b) was checked on a throwaway container on Docker 29.

The image-only install (ADR 0020) adds checks of its own, run by `scripts/test-install-check.sh` in CI:
the release assets build with a verifying `SHA256SUMS`, the runbook's download loops list exactly the
assets, the `classbeat-db` image holds the repository's SQL, and the released `check-prod-compose.sh`
passes and fails (a local compose edit, a bad `.env`, each wrong secret mode or group, the LAN
overlay's address, `--probe` and `--rollback-check` against a stub `docker`) from a folder with no `docs/`.

Not checked: the release run itself (the first real tag builds both images and attaches the assets;
agents cannot push tags), pulling the private images with a real token, and a running prod stack
behind a real swag (the image, Cloudflare, the certificate, the
real mail path and the worker's start-up), which is the post-deploy checklist above.
