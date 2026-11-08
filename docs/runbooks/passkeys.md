# Passkeys: the owner's runbook

[ADR 0019](../adr/0019-passkeys.md) (issue #311). Passkeys are an **additional** way to sign in and to
pass re-authentication; a password or a linked provider always remains. This page is the owner's
checklist for choosing the RP ID, switching them on and off, what users see, the one known residual
and the real-phone check. It names no secret: a passkey has none on our side (only a public key), and
no setting here is a key.

Settings, routes and behaviour for developers are in
[developer-commands.md](../developer-commands.md) (the "Passkeys" paragraph); the threat rows are
T113 to T120 in [the threat model](../security/threat-model.md).

## 1. The RP ID, per environment

The RP ID is the name every passkey is bound to. **Choose it once per environment: a later change
makes every existing passkey of that environment useless** (users add new ones; nothing is lost
but the old passkeys).

| Environment | `CLASSBEAT_PUBLIC_BASE_URL` | `CLASSBEAT_WEBAUTHN_RP_ID` |
|---|---|---|
| Production | `https://classbeat.eu` | `classbeat.eu` |
| Test and beta | `https://classbeat.pernemark.se` | `classbeat.pernemark.se` |

- Use the **exact host, never a parent** (`pernemark.se` would let every subdomain's page ask for
  these passkeys). The app refuses to start unless the RP ID is a lowercase DNS name equal to the host
  of `CLASSBEAT_PUBLIC_BASE_URL` or a parent of it, never an IP address, a scheme, a port or a path.
  Do not rely on that check to pick the host for you.
- The RP ID and the origin come only from these settings. No request header (`Host`,
  `X-Forwarded-Host`, ...) changes them.
- The settings are checked in every role (web, worker, CLI), so a wrong value stops all of them at
  start with a message that names the rule, not the value.
- The e2e stack uses `localhost` and is switched on there (`compose.e2e.yaml`); nothing else is on.

## 2. Switching on and off

Passkeys are **off by default** (`CLASSBEAT_PASSKEYS` unset or false) and are switched on per
environment.

`compose.prod.yaml` passes both settings to `web` only (issue #790), from `.env` under the short names
`PASSKEYS` (default `false`) and `WEBAUTHN_RP_ID` (default empty, which is unset: web then refuses to
start with passkeys on, so a forgotten RP ID cannot go live). `scripts/check-prod-compose.sh` allows
exactly these two names on `web`, refuses them on any other service and refuses `PASSKEYS` defaulting
to anything but `false`. A server needs a release that includes this change.

1. Set `WEBAUTHN_RP_ID` (section 1; the value for `CLASSBEAT_WEBAUTHN_RP_ID`) and `PASSKEYS=true` in
   the environment's `.env` (see `prod.env.example`), then redeploy as in [vps-deploy.md](vps-deploy.md). A bad RP ID stops the stack at start;
   fix the value and start again.
2. Check the swag site keeps today's `Permissions-Policy` line, which names neither
   `publickey-credentials-get` nor `publickey-credentials-create` (both then default to the site
   itself, which passkeys need). `scripts/check-prod-compose.sh` refuses a sample that sets either to
   `()` or `*`, and one whose allowlist leaves out `self`; if you edit the header on the server by hand, keep it that way.
3. Do the real-phone check (section 5).

**Switching off:** set `CLASSBEAT_PASSKEYS=false` (or remove it) and redeploy. Then:

- Register, sign-in, re-authentication and rename answer **404 `feature_off`**, and the sign-in page
  and the re-authentication prompt no longer show a passkey button.
- **List, remove, the password reset and `admin mfa-reset` still work**, so no user is stuck with a
  passkey they cannot see or remove, and a reset still deletes passkeys.
- Nothing is deleted by switching off. Switching on again with the same RP ID makes the passkeys work
  again.

Every account keeps its password or provider sign-in the whole time.

## 3. What users see

- **Account page, "Passkeys":** the list with a name (optional; for example "Work laptop"), "Synced"
  or "This device only", and when it was last used; "Add a passkey", rename and remove. At most 10 per
  user. Adding, renaming and removing need recent authentication (and the TOTP step-up for users who
  have TOTP), like the other sensitive actions. Every addition and removal is mailed and shows in the
  security activity list.
- **Sign-in page:** "Sign in with a passkey" (the browser also offers a saved passkey in the email
  field). No username is needed.
- **Re-authentication:** "Confirm with a passkey" next to the password. A passkey is never the
  TOTP step-up, and an admin still enters TOTP.
- **Password reset page:** lists the account's passkeys with the linked providers, each removable in
  the same step. Whatever the user does not keep is deleted.
- **After signing in with a phone or security key** ("use a phone" on another computer): the message
  "Signed in with your phone or security key; sign out when you are done." The session then lasts at
  most 24 hours and the computer is not remembered as a known device.
- **A passkey stops working** (removed, or the RP ID changed): "That passkey is not registered here any
  more. Sign in another way." The user signs in with the password or a provider and adds a new one.

## 4. The `passkey_busy` residual

Passkey sign-in start is rate limited per address (60 per 5 minutes), per IPv6 /48 and in one global
bucket (20,000 per 5 minutes). When the global bucket is full, the start answers **503 `passkey_busy`**
for everyone, and the page tells the user to use the password or a provider (those are never affected).

- About **334 IPv4 addresses or 67 IPv6 /48 networks**, each sending a steady stream, can keep the
  global bucket full in 5-minute windows. This is accepted for the beta (T119).
- **What to look for:** in the access log, many `429` answers on `/api/auth/passkeys/signin/start`
  from a few addresses (the per-address bucket working), and any `503` on that path with
  `passkey_busy`. A few 429s are one noisy client. Sustained `passkey_busy` is the residual: block
  the offending networks at swag or Cloudflare, or switch passkeys off (section 2), and tell users to use
  the password. Nothing else needs undoing; the buckets empty on their own within 5 minutes.
- Never log or paste a request body while looking: they are not logged, and none holds anything you need.

## 5. The owner's real-phone check

Agents test passkeys on fakes only; no automated test uses a real authenticator or a real phone. After
switching on in an environment (section 2), you do this once, with your own account. It needs two
computers and a phone with a screen lock, Bluetooth on and a passkey provider (for example iCloud
Keychain or Google Password Manager).

1. On **computer A**, sign in with your password, open the account page and choose "Add a passkey".
   Approve with the computer's screen lock. It appears in the list.
2. Sign out. On computer A, choose "Sign in with a passkey" and approve. You are signed in.
3. Optionally add a passkey on the **phone** too ("Add a passkey" in the phone's browser).
4. On **computer B** (never signed in to classbeat), open the sign-in page, choose "Sign in with a
   passkey" and, in the browser's dialog, "use a phone" (or "a phone or tablet"). Scan the QR code with
   the phone, approve there, keep Bluetooth on.
5. Expected: you are signed in on computer B and see "Signed in with your phone or security key; sign
   out when you are done." Sign out.
6. Remove the test passkeys you do not want to keep (the account page, then mail arrives for each).

If step 4 shows no "use a phone" option or the phone cannot connect, see section 6. Record the result
in [status.md](../status.md); agents will not run this check for you.

## 6. Hybrid (the QR code), documented

"Use a phone" is the browser's own **hybrid** (cross-device) transport: the browser shows the QR code
and the two devices talk over the internet and prove they are close by Bluetooth. classbeat has **no QR
flow of its own** and nothing on the server is specific to it: the assertion reaches the same sign-in
route as any other, and a phone assertion is recognised only by what the browser reports (`cross-platform`).
That report comes from the client, so it is used only to make the session stricter (24 hours, no
known-device cookie), never to grant anything.

Things that make it fail, none of them classbeat's: Bluetooth off on either device, a network that
blocks the browser's cross-device relay, or a browser without hybrid support. Production sites need no
extra firewall rule on our side.

## 7. Locked-out users and recovery

- A user who lost every passkey signs in with the password or a provider; passkeys never replace them.
- A user who lost their password uses the email reset; the reset page lets them remove passkeys they
  do not recognise, and every one they do not keep is deleted.
- For a suspected stolen account, `admin mfa-reset` removes the TOTP, recovery codes **and passkeys**
  and mails a reset link, as in [ADR 0003](../adr/0003-accounts-and-sessions.md) section 7.
