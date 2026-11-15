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
3. **Promote:** `step promote`, then restart web. The next key signs; the old one is retired
   and stays published.
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
