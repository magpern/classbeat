# Swedebeat connection: the owner's steps

[ADR 0015](../adr/0015-swedebeat-connection.md) and [ADR 0035](../adr/0035-swedebeat-gateway.md).
classbeat signs a user in to SwedeBeat **through the owner's SwedeBeat gateway**
(`magpern/sb-gateway-service`) and never calls SwedeBeat itself. It holds no SwedeBeat key: the
gateway does. The connection (connect, status, disconnect) and the match are built and tested against
a fake gateway only. **Live use is yours**: agents make no live gateway or SwedeBeat call and never
create, read or ask for the client key.

## Rules

- The client key is the value the gateway lists for classbeat in its `CLIENT_API_KEYS`. It lives in
  the gateway's configuration and in `secrets/swedebeat_gateway_key` on the classbeat server (web and
  the worker mount it, nothing else). You keep a copy as the repository secret `SB_CLIENT_API_KEY`; no
  workflow reads it. It is never in git, an image, a workflow, a log, an issue or this file.
- Never paste a Swedebeat password, a bearer or the client key into chat, an issue or a log.
- Each live step below needs the owner's own go-ahead for that step; log it under "Live activation
  log" in [docs/status.md](../status.md).

## 1. The gateway (once)

On the gateway (ADR 0035 section 10):

- list classbeat's key in `CLIENT_API_KEYS`;
- keep its writes off (`allow_swedebeat_writes` false, the default);
- **reserve `192.168.1.222`** for the server (a DHCP reservation or a static address), so the address
  can never be handed to another device (the LAN path is plain http);
- set `GATEWAY_BIND` so the port listens on that address, and keep it on the LAN or the docker bridge,
  never on a public interface while it is plain http;
- optionally raise `SESSION_TTL_SECONDS` (default 24 h: a user enters the password again after it).

## 2. The classbeat server

- **The file `secrets/swedebeat_gateway_key` must exist**, empty or filled, before any release that
  carries #1077 (SG-1) is started: `web` and the `worker` mount it, and compose refuses to start a
  service whose secret file is missing (`( umask 027; touch secrets/swedebeat_gateway_key )`;
  [vps-deploy.md](vps-deploy.md) section 5). Empty means no key: SwedeBeat is "not available".
- **The key.** Write it without echoing it:
  `( umask 027; read -rs S && printf '%s' "$S" > secrets/swedebeat_gateway_key; unset S )`, then
  `ls -ln secrets/swedebeat_gateway_key` (mode `-rw-r-----`, group 10010, as the other secret files).
  It must be 16 to 256 printable ASCII characters without whitespace; anything else stops web and the
  worker at start (the message names the file, never the value).
- **The URL** is `SWEDEBEAT_GATEWAY_URL` in `.env` (production `http://192.168.1.222:7355`). Plain http
  is accepted only to a loopback or private IP address with a port; otherwise it must be https to a
  public DNS name. A value that breaks a rule stops web and the worker at start, and the message names
  the rule, never the value. The containers reach the host's LAN address over docker's default bridge.
- SwedeBeat is available only with both the URL and a key.

## 3. Switch it on

- `SWEDEBEAT_ENABLED=true` in `.env` (web and the worker; default `false`).
- The admin area (Settings > Swedebeat) can switch it off for everyone, never on past this variable.
- Each user turns on their own "I use Swedebeat" preference by connecting.
- Without the URL or the key the account card says "Swedebeat sign-in is not available on this
  server." and connect answers 503 `app_key_unavailable` before anything is sent. A gateway that
  answers `unknown client` (a wrong key) gives the same answer and the log line
  `swedebeat gateway refused the client key`; no user's connection is touched. The line
  `swedebeat gateway not configured` means web started without the gateway's origin, so nothing was
  sent (check `SWEDEBEAT_GATEWAY_URL` and restart web).

## 4. One live check (owner's go-ahead)

With your own Swedebeat account on your own stack:

- connect (a wrong password first, to see the message). **Every connect signs the account out of the
  Swedebeat app on your phone**: the form says so and needs "I understand" ticked;
- see the status and the email;
- match a short real list and read `found`/`unsure`/`missing` against the app
  ([swedebeat-match.md](swedebeat-match.md));
- disconnect.

Record whether the connection's expiry matches the gateway's `SESSION_TTL_SECONDS`.

## Rotation or a leak of the client key

1. List the new key in the gateway's `CLIENT_API_KEYS`.
2. Write it into `secrets/swedebeat_gateway_key` as in section 2.
3. Restart web and the worker (`docker compose up -d --wait`).
4. Remove the old key from the gateway.

A leaked key lets a caller on the LAN use the gateway with a user's bearer or password, never by
itself (threat model T104).

## After the SG-4 release (owner steps, never an agent's)

Until SG-4 (#1080), release images were built with SwedeBeat's own app key from the GitHub secret
`SWEDEBEAT_APP` (ADR 0015 section 4). Since SG-4 no workflow reads that secret and no image holds the
key. **Only after a release that carries SG-4 exists** (before it, the release workflow needs the
secret):

1. delete the GitHub secret `SWEDEBEAT_APP` (`gh secret delete SWEDEBEAT_APP --repo magpern/classbeat`);
2. delete the older release image tags on GHCR that carry the key
   (`gh api -X DELETE /user/packages/container/classbeat/versions/<id>` or the package page);
3. on a server, delete the leftover `secrets/swedebeat_app` once the SG-4 release runs
   ([vps-deploy.md](vps-deploy.md)); `./check-prod-compose.sh` refuses a compose file that still mounts
   a `swedebeat_app` secret.
