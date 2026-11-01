# Outgoing mail through Proton Mail Bridge: the owner's steps

Owner decision of 2026-10-31 (#352): account mail (verification, password reset) goes through Proton
Mail Bridge, the owner's own image `ghcr.io/magpern/proton-bridge`, as a separate compose service on a
private network, not part of classbeat's compose files. Agents do not touch Proton accounts.

## 1. Rules

- Use a **dedicated Proton account** for classbeat, never a personal mailbox: Bridge holds a logged-in
  session and decryption keys.
- Keep Bridge's data volume private (not shared, not backed up to a public place).
- **Bridge can need a login again** (session expired, password changed, Proton asked for a check). All
  mail then stops silently: classbeat only logs `mail not sent (MailError)`. Check Bridge's logs and
  log in again; do this when sign-up mails or resets do not arrive.
- Check Proton's terms for application mail through Bridge.

## 2. Settings in classbeat

| Setting | Value |
|---|---|
| `SMTP_HOST` | `proton-bridge` (the Bridge service's name) |
| `SMTP_PORT` | `2025` |
| `SMTP_TLS` | `starttls` |
| `SMTP_USERNAME` | the Bridge account's username, as Bridge shows it |
| `SMTP_SENDER` | an address of the dedicated account |
| `secrets/smtp_password` | the **Bridge-generated** password (not the Proton password) |
| `SMTP_CA_FILE` | `/run/smtp-ca/bridge.pem` (section 3) |

`web` must share a Docker network with the Bridge service (an external network that only these two
join, section 3). The worker sends no mail and gets neither the network nor the certificate.

## 3. Trust Bridge's self-signed certificate (pin it)

Bridge serves a self-signed certificate, so classbeat's normal verification fails. Do **not** turn
verification off: `SMTP_CA_FILE` (`CLASSBEAT_SMTP_CA_FILE`) makes classbeat verify the server against
that one certificate file instead of the system store. The file should hold Bridge's own (leaf)
certificate only, not a CA: a CA would trust everything it signs for that host. The hostname check stays on.

1. Export the certificate (the public part only, never the key) from the Bridge container, for example
   `docker cp proton-bridge:<path to Bridge's cert.pem> ./bridge.pem`; check it with
   `openssl x509 -in bridge.pem -noout -subject -ext subjectAltName -enddate`.
2. It must name the host classbeat connects to: `proton-bridge` has to appear as a DNS name in its
   subject alternative names. If it only names `localhost` or `127.0.0.1`, classbeat refuses it by
   design; fix the Bridge side (a certificate for the service name), do not weaken the check.
3. Mount it **read-only** into `web` and join `web` to the network Bridge is on, with a second compose
   file `compose.bridge.yaml` next to `compose.prod.yaml` (not committed; adjust the network name):
   ```yaml
   services:
     web:
       volumes:
         - ./smtp-ca/bridge.pem:/run/smtp-ca/bridge.pem:ro
       networks:
         - bridge-mail
   networks:
     bridge-mail:
       external: true
       name: proton-bridge-net   # the private network the Bridge service is on
   ```
   Every prod command then uses both files, for example
   `docker compose -f compose.prod.yaml -f compose.bridge.yaml up -d --wait` (a `compose.override.yaml`
   is **not** picked up when `-f` is given, so do not use that name). `migrate` and `worker` get neither
   the variable nor the mount.
4. Set `SMTP_CA_FILE=/run/smtp-ca/bridge.pem` in `.env`. `web` then refuses to start unless the file is
   a readable PEM certificate (and refuses it with `SMTP_TLS=none`). Unset, nothing changes.

When Bridge's certificate is renewed or regenerated, export and mount the new file and restart `web`;
until then mail fails with `mail not sent (MailError)` (a different certificate is refused).

Threat model: T112 in [docs/security/threat-model.md](../security/threat-model.md).
