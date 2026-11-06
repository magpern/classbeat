# ADR 0024: Connect MOWL by scanning a QR code (#647)

Status: Proposed (accepted when its PR, which closes #647, merges after a security-critical
`PASS`). The feature ships **off** (`CLASSBEAT_MOWL_QR_ENABLED`, default false) until the owner's
one live scan.

Related: [ADR 0004](0004-connections-and-encryption.md) (connections: encrypted storage, categories,
`repository.save`), [ADR 0006](0006-agent-runs.md) §9 and
[ADR 0016](0016-swedebeat-music.md) §4 (the outbound allowlists), [ADR 0003](0003-accounts-and-sessions.md)
§6 (recent authentication and step-up), [ADR 0010](0010-live-activation-permissions.md) (MOWL terms posture).

## Context

Today a user connects MOWL by typing the MOWL email and password into classbeat
(`connections/mowl_account.py`). The MOWL Windows app also signs in by showing a QR code that
the MOWL phone app scans. Issue #647 records the flow, captured on 2026-11-05 (tokens compared by
hash only):

1. `GET https://api.mowl.com/v1/qr/create/authentication`, anonymous, answers a ticket:
   `QrTicketID`, `Token` (a GUID: the key of an MQTT topic, not the session token), `QrImageUrl`
   (`https://media.mowl.com/qr/ticket/<QrTicketID>`, public), `DateExpired` (15 minutes) and
   `MQTTConnectionInfo` (broker `mq.intelligent-cycling.com`, ports 1883, 8883 for TLS and 8000,
   username and a per-ticket password, `MQTTFlowVersion` 1, `DisableSSL`).
2. The app shows the image, connects to the broker (MQTT 3.1.1) and subscribes to
   `qrticket/<Token>` at QoS 0.
3. When the phone scans, the broker publishes one message on that topic: 36 bytes of plain text,
   **the session token itself**. The app disconnects and uses it as after a password sign-in.

So **whoever can subscribe to `qrticket/<Token>` gets the user's session**; the topic key and
the broker password are the only protection. The Windows app also **falls back to plain MQTT on
1883** when the TLS handshake fails, and the broker accepts that.

The issue proposed four PRs (a `mowl-workouts` client with an MQTT library, the egress rule, the
backend, the frontend). The Architect asked for one classbeat PR.

## Decision

**1. One PR, MQTT in classbeat, no new dependency.** The flow is implemented in
`classbeat.connections` instead of `mowl-workouts`: the ticket request goes through the library's
existing `api.call` (urllib, so the web's urllib allowlist applies), and the MQTT subset the flow
needs (CONNECT, SUBSCRIBE to one exact topic, one PUBLISH, PINGREQ, DISCONNECT) is about 200
lines in `connections/mowl_mqtt.py` over an `asyncio` stream pair. A third-party MQTT client
(paho-mqtt, aiomqtt) would open its own socket, which classbeat's egress guard can neither see
nor restrict; owning the socket keeps the one rule of section 4 enforceable and adds no
dependency to audit. `mowl-workouts` is unchanged (its CLI keeps the password sign-in).

**2. The ticket (`mowl_qr.request_ticket`, `parse_ticket`).** Anonymous (no `authorization`).
The answer is refused (`unknown`, no text) unless the ticket id is a positive integer, `Token` a
GUID, `QrImageUrl` exactly `https://media.mowl.com/qr/ticket/<id>`, the broker named
`mq.intelligent-cycling.com` with TLS port 8883 and flow version 1, and the username and
password short printable ASCII. `DisableSSL`, `MQTTServerPort`, the websockets port and
`ClientID` are never used. The key and the password live in a `QrTicket` that has no `repr`,
cannot be pickled or copied, and never leaves the web process: **the browser never sees the
topic key, the broker password or MOWL's ticket id.**

**3. The image, from our origin.** The CSP allows images from `'self'` and `i.scdn.co` only, so
the web fetches the public QR image (`egress.mowl_qr_image_async_client`: `media.mowl.com` alone,
https on 443, no redirect, no default header, `trust_env=False`, a 10 s total deadline, web only,
no argument) and serves it at `GET /api/connections/mowl/qr/{id}/image` (`no-store`,
`nosniff`) while the wait runs. The body is read raw (identity encoding only, at most 256 KiB)
and its type is taken from its first bytes: PNG, JPEG, GIF or WebP; never SVG or anything else.

**4. The broker: TLS on 8883 only, the one non-HTTP egress rule.** `egress.mowl_mqtt_open()`
connects to `(mq.intelligent-cycling.com, 8883)` exactly (`MQTT_ALLOWLIST`, checked by
`check_mqtt` before the connect), with `tls_client_context()` (hostname check, `CERT_REQUIRED`,
system trust store, no key log) and TLS 1.2 at least, in the web process only, connect and
handshake within 15 s. **A failed handshake, an unverified certificate or a refused connect is
an error; nothing else is tried** (never 1883, never another port or host). It takes no
argument, so neither a caller nor MOWL's answer chooses the host, the port or switches TLS off;
the static egress guard refuses an argument to it and any other raw connection in `classbeat`.
The client subscribes to the one exact topic of its own ticket (a wildcard is refused when the
packet is built), uses a fresh client id of the app's form (`w0-<digits>-888`), accepts packets
of at most 4 KiB, and takes the first message on its topic only if it is exactly 36 ASCII bytes
forming a GUID; anything else fails the wait (`unknown`).

**5. The wait: one per user, bound to the starting session, in memory.** `POST
/api/connections/mowl/qr` needs recent authentication and, with TOTP, step-up (as the password
connect), is rate limited before MOWL is called (10 per user and 30 per IP per 15 minutes,
`auth.mowl_qr_buckets`; every start counts), then requests the ticket, fetches the image and
starts the wait (`QrWaits.start`), cancelling the user's earlier wait. It answers
`{ticket_id, image, expires_at}`, where `ticket_id` is **our own random id**. The web process
holds at most 100 running waits (a broker connection each); over it a start is `rate_limited`.
The wait ends at 15 minutes (`expired`), on a new start, on `DELETE` (the page was left), on a
broker or TLS failure (`failed`) and on shutdown. The web runs as one process (as the job event
hub already assumes), so in-memory state is enough; nothing about a wait is written to the
database or a log beyond the outcome word and a category.

**6. Storing the token: on the starting session's poll.** The page polls
`GET /api/connections/mowl/qr/{id}` (our API, never MQTT from the browser). Every route but the
start finds a wait **only for the user and the session that started it**; another user's,
another session's or an unknown id is 404 `not_found`. Once the phone has scanned, the next poll
takes the token (once) and stores it exactly as the password connect does: `repository.save`
(encrypted under the connection KEK, row lock, reconnect keeps the row id), audit event
`connection_connected` with `method: qr` (`connection_failed` with the category otherwise). The
recent-authentication check is the start's: the store rides on that same session, so signing it
out ends any chance to store, and a token no poll takes within 60 s is dropped from memory and
never stored. The poll is a `GET` that may write: it only completes what the same session started with the
CSRF-checked `POST`, its id is a 192-bit random value only that session was given, and what it
stores is the token MOWL handed to that session's own ticket, so a forged request can at most
finish the victim's own connect a little earlier. The token, like a password-flow token, has no known expiry (ADR 0004 §6).

**7. Switch and page.** `CLASSBEAT_MOWL_QR_ENABLED` (compose `MOWL_QR_ENABLED`, web only, default
false); off, every QR route answers 404 `feature_off` before any call, and `GET /api/connections`
carries `mowl_qr_enabled` for the page. The account page offers "Scan with the MOWL app" in the
wider layouts only (rail and sidebar, ADR 0013 §4; not the phone layout, since a phone cannot
scan its own screen), with the code, a countdown, a retry and a cancel when it is stopped or the
page is left; the password form stays the fallback in every layout. The page shows the image
only from our own route for that ticket (`/api/connections/mowl/qr/<id>/image`; any other answer
is refused) and polls our API every 2 s.

## Alternatives

- **The `mowl-workouts` client with paho-mqtt or aiomqtt** (the issue's split): a new dependency,
  and a socket outside classbeat's egress guard. Rejected for the server; the library may still
  add a QR sign-in for its CLI later, with its own review.
- **MQTT from the browser over websockets (port 8000):** hands the topic key and the broker
  password to the browser and needs a CSP and egress exception. Rejected.
- **A worker job for the wait:** the worker's allowlist would need the broker, and the token
  would cross the queue; the wait is short, per session and interactive. Rejected.
- **Following the ticket's `DisableSSL` or the 1883 fallback:** sends the session token in clear
  text. Rejected.

## Consequences

- The web process gets two outbound paths it did not have: `media.mowl.com:443` (public image,
  no token sent) and `mq.intelligent-cycling.com:8883` (TLS; receives the session token). Both
  are literal, code-only rules with their own tests (`test_egress_mowl_qr.py`).
- The flow signs in as the Windows app, the same posture and terms risk as the password sign-in
  (ADR 0010). If MOWL changes the flow, the ticket check refuses it (`unknown`) and the password
  form still works.
- A restart of the web drops every running wait; the user starts again.
- Threat model T125 records the threats and their controls.
- **Owner step before switching it on:** one live ticket created and scanned by the owner on a
  stack with `MOWL_QR_ENABLED=true` (no agent runs it).
