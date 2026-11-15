# The player sign-in provider: secrets and key rotation

[ADR 0034](../adr/0034-classbeat-player-connection.md) §1 (issue #1056). classbeat is the OpenID
Connect provider for classbeat-player: one client, one exact redirect address, the authorization
code flow with PKCE. This page is the operator's checklist for its three secret files and for
rotating the ID token signing key. It never shows a secret value: every step writes into a file.

The provider is **off** while neither `CLASSBEAT_OIDC_PLAYER_CLIENT_ID` nor
`CLASSBEAT_OIDC_PLAYER_REDIRECT_URI` is set; every `/oidc` route then answers 404. With both
set, web refuses to start unless all three files below are present and well formed (the error
names the file, never its content). Setting only one of the two is refused too.

> Mounting the three files into `web` and `maint` in the deploy compose files is a separate
> tooling change; until it lands, leave both settings unset in production.

## 1. The three secret files

All three live in `secrets/` with the same owner, group and mode as the other secret files
(group 10010, mode 640; [vps-deploy.md](vps-deploy.md), "Secrets"). Run each step from
`/opt/classbeat` with `umask 027`.

**The client secret** (shared with the player's operator; classbeat keeps only its hash):

```bash
( umask 027
  S=$(head -c 32 /dev/urandom | base64 | tr '+/' '-_' | tr -d '=\n')      # 43 base64url characters
  printf '%s' "$S" | sha256sum | cut -d' ' -f1 > secrets/oidc_client_secret_hash
  printf '%s' "$S" > /dev/shm/player_client_secret                     # hand over, then delete
  unset S )
```

Give `/dev/shm/player_client_secret` to the player's operator over the agreed channel, then
`shred -u /dev/shm/player_client_secret`. A secret of any other shape than 43 base64url
characters never matches. To replace it, repeat the step and restart web; the old secret stops
working at once.

**The pairwise key** (`sub` = HMAC over the client id and the user id). **It never rotates**:
a new key changes every user's `sub` and orphans every player account.

```bash
( umask 027; head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n' > secrets/oidc_pairwise_key )
```

Keep it in the backup with the other secret files. If it is lost, every player account must be
linked again.

**The signing keys** (ES256, P-256): start an empty set with one current key.

```bash
( umask 027
  docker compose --profile maint run --rm -T maint python -m classbeat oidc keys add-next \
    < /dev/null > secrets/oidc_signing_keys.new
  mv secrets/oidc_signing_keys.new secrets/oidc_signing_keys )
```

Restart web after any change to a secret file: the provider reads them once at start.

## 2. Rotating the signing key

`python -m classbeat oidc keys <step>` reads the key set on stdin and writes the new set on
stdout. It needs no database. A step that writes keys refuses when stdout is a terminal, so
always redirect it into a new file. `list` prints only ids, states and times.

The player caches the key set for 24 hours, so a new key is published **at least 24 hours and
5 minutes before it signs**, and a retired key stays published for 24 hours after its last
signature.

```bash
run() { docker compose --profile maint run --rm -T maint python -m classbeat oidc keys "$@"; }
step() {   # step <name>: apply it to the live set, then swap the file in
  ( umask 027; run "$1" < secrets/oidc_signing_keys > secrets/oidc_signing_keys.new ) &&
    mv secrets/oidc_signing_keys.new secrets/oidc_signing_keys
}
run list < secrets/oidc_signing_keys
```

1. **Publish the next key:** `step add-next`, then restart web. `/oidc/jwks` now lists both keys;
   the current one still signs. There can be only one next key.
2. **Wait at least 24 hours and 5 minutes.** `promote` refuses earlier and says so.
3. **Promote:** `step promote`, then restart web (and the worker, when player events are on,
   section 4: it signs the logout tokens with the same key). The next key signs; the old one is
   retired and stays published.
4. **After 24 more hours, retire:** `step retire`, then restart web. It drops every retired key
   whose 24 hours have passed (earlier, it keeps them and changes nothing).

If a step is refused, `secrets/oidc_signing_keys.new` is empty and the `mv` does not run; the
live file is unchanged. Delete the empty file.

**A leaked signing key:** a new key cannot sign at once (the wait protects the player's cache).
Instead stop the provider (unset both settings and restart web), tell the player's operator, then
rotate as above and switch it back on after step 3.

## 3. A user's subject for the player's operator

```bash
docker compose --profile maint run --rm maint python -m classbeat admin player-subject <email>
```

It prints the user's pairwise `sub` (player ADR 0010 §4) and writes one `admin_audit` row
(`player.subject`). It changes nothing else.

## 4. Player events (the worker)

ADR 0034 §2 (issue #1058). When a user deletes their account, or every session of theirs ends
(ADR 0003 §3), classbeat writes an event to the `player_events` outbox, but only for a user the
player ever signed in. The worker delivers it to the player as a signed OpenID Connect
back-channel logout token, POSTed to `<CLASSBEAT_PLAYER_BASE_URL>/api/classbeat/events`.

- **Off** while `CLASSBEAT_PLAYER_BASE_URL` is unset. Events are still written and, after 7
  days, marked failed; they then show under **Admin, Jobs, Undelivered player events** and are
  purged 30 days later.
- **On:** set `CLASSBEAT_PLAYER_BASE_URL` on the worker to the player's origin: `https`, a public
  DNS name, no user, path, query or fragment (an IP, `localhost`, `.local` and the like are
  refused). The worker then needs `CLASSBEAT_OIDC_PLAYER_CLIENT_ID` (the token's `aud`) and the
  `oidc_signing_keys` file, and refuses to start without them. The web process never reaches the
  player.
- **Retries:** backoff from 30 seconds to 6 hours, at most 40 attempts and 7 days; every attempt
  signs a new token with the event's one `jti`, so the player sees one event.
- **After a key rotation step** (section 2), restart the worker too.
- **Writing events** needs `oidc_pairwise_key` on web (the user's delete, sessions ending) and on
  maint (the operator's erase and the CLI's revocations). Without it, an account delete or a
  revocation for a user the player knows is refused rather than losing the event.

**An undelivered event.** A failed event means the player never heard of the change, and its
account stays as it was. For an `account_deleted`, the user row is already gone, so section 3
can no longer compute the subject. Use the list instead:

1. Open **Admin, Jobs, Undelivered player events**. Each row shows the event, the user's
   pairwise **subject** (`sub`), when it was written, when delivery gave up, the attempts and
   the last error category. Only failed events are listed, never a pending one and never the
   event's `jti`. Every opening of the list is an `admin_audit` row (`player.event.list`).
2. Look at the last error. `status_4xx` usually means the player refused the token: check the
   client id and the published keys with the player's operator. `network`, `timeout` and
   `status_5xx` mean the player could not be reached.
3. Send the player's operator the subject and the event, over the channel you agreed with them.
   For `account_deleted` they delete that account (player ADR 0010 §8, `admin account delete`);
   for `sessions_revoked` they end its sessions. Send nothing else: the subject identifies the
   account to the player and to no one else.
4. Nothing to clear here: the row is purged 30 days after delivery gave up.

> Mounting `oidc_signing_keys` into the worker, and `oidc_pairwise_key` into web and maint, in the
> deploy compose files is a separate tooling change; until it lands, leave
> `CLASSBEAT_PLAYER_BASE_URL` unset in production.

