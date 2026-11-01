# Swedebeat connection: the owner's steps

[ADR 0015](../adr/0015-swedebeat-connection.md). The connection (connect, status, disconnect) is built
and tested against a fake Swedebeat only. **Live use is yours**: agents make no live Swedebeat call and
never create, read or ask for the app key.

## Rules

- The app key is Swedebeat's. It lives in the GitHub secret `SWEDEBEAT_APP`, in release images, and
  (optionally) in a `swedebeat_app` secret file on the web container. Never in git, a pull-request or
  CI image, a log, an issue or this file.
- Never paste a Swedebeat password, token or the key into chat, an issue or a log.
- Each live step below needs the owner's own go-ahead for that step; log it under "Live activation
  log" in [docs/status.md](../status.md).

## 1. Create the secret (once)

The key is a JSON file with exactly `key` (32 characters) and `iv` (16 characters), the same content as
the `mowl-workouts` repository's `SWEDEBEAT_APP` secret. From the directory that holds it:

```bash
gh secret set SWEDEBEAT_APP --repo magpern/classbeat < swedebeat_app.json
```

`gh` reads the file from standard input; nothing is printed. Delete the local file afterwards if you
do not need it. `.gitignore` and `.dockerignore` already ignore any `swedebeat_app*` file.

## 2. Release

Cut a release as in AGENTS.md ("Releasing"). `release.yml` checks the secret (present, JSON, 32 and 16
characters) before it builds, and the `Dockerfile` checks it again; a missing or malformed secret fails
the release with an error that names the problem, never the value. Pull-request, CI and e2e builds never
receive it (`backend/tests/test_swedebeat_key_guard.py` and the CI step "Check the Swedebeat app key is
not in the image" keep it so).

## 3. Switch it on

- `SWEDEBEAT_ENABLED=true` in the web service's environment (`.env` of the stack; default `false`).
- The admin area (Settings > Swedebeat) can switch it off for everyone, never on past this variable.
- **Home server, without a rebuild:** put the key file at `secrets/swedebeat_app` (same JSON) and mount
  it as the Docker secret `swedebeat_app` on `web` only (`compose.yaml` and `compose.prod.yaml` do; an
  empty file means no key). The file must exist even when empty, or compose fails at `up`: an
  existing stack (the home beta, an older worktree) runs `touch secrets/swedebeat_app` once
  (`scripts/worktree.sh init` does it for a checkout). A file there wins over the one in the image. The worker must not get it
  (it refuses to start if it can read it).
- Without any key the account card says "Swedebeat sign-in is not available on this server." and
  connect answers 503 `app_key_unavailable`.

## 4. One live check (owner's go-ahead)

With your own Swedebeat account on your own stack: connect (a wrong password first, to see the message),
see the status and the email, connect again from the same page (same device), disconnect. Read off and
record:

- the real wording Swedebeat answers when another device (your phone app) holds the session; compare it
  with the phrases in `classbeat.connections.swedebeat_account` (`_DEVICE_PHRASES`) and fix them if
  they differ (ADR 0015 §5);
- whether a token ever expires (first 401 or 403), as for MOWL (ADR 0004 §6).

## Rotation or a leak

1. Get the new key; `gh secret set SWEDEBEAT_APP --repo magpern/classbeat < swedebeat_app.json`.
2. Cut a new release and deploy it.
3. Delete the old image tags from GHCR (`gh api -X DELETE /user/packages/container/classbeat/versions/<id>`
   or the package page); anyone who could pull them could read the key.
4. On the home server, replace `secrets/swedebeat_app` the same way and restart web.
5. Users reconnect only if Swedebeat invalidated their tokens.
