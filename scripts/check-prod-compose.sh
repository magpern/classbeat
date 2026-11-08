#!/usr/bin/env bash
# Checks the production compose files and the sample swag site config with dummy values (#341, #492).
# No secret is ever read, nothing contacted but the local Docker daemon (and its registry for the
# nginx image).
#   scripts/check-prod-compose.sh        (needs docker, jq; run from anywhere)
# 1. `docker compose config` accepts compose.prod.yaml alone (swag on a shared Docker network) and
#    with compose.prod.loopback.yaml (a proxy on the host, web on 127.0.0.1);
# 2. the invariants of the production stack hold for both (with the OAuth settings of #507: the
#    client ids only from their variables, the client secrets as files on web only, never a value
#    in the rendered files; #511: every service that mounts a secret has group 10010 and a non-root
#    user, each secret is secrets/<its own name>; #517: DEFAULT DENY, every key of the rendered files
#    (all profiles) must be on the allow-list of check_keys, and env_file, label_file, profiles,
#    extends and include are refused in the files' text (a lint); #610: each service's variables by
#    name (whatever an env_file, however spelled, adds is refused, its name never printed) and the
#    top-level networks by key), and compose.prod.yaml's defaults leave every OAuth client id empty;
# (the SQL the database services run is in the release's classbeat-db image: no bind mounts, ADR 0020)
# 3. a self-test: each unsafe mutation of the rendered files must fail step 2 (fails closed);
# 4. the owner's real PROXY_IP, when one is set (their shell, else PROD_CHECK_ENV_FILE, default the
#    checkout's .env): compose.prod.yaml rendered with it must trust one private IPv4 address (#495);
# 4b. that env file (and, in a checkout, .env.example) holds no *CLIENT_SECRET* variable; and in the compose
#    files (the three production ones, in a checkout also the dev compose.yaml) `client_secret` appears
#    only as the three secret names and their ./secrets/ files, in check_oauth_files (step 2): a client
#    secret is a file under secrets/ only;
# 4c. on the server (that env file's COMPOSE_FILE names compose.prod.yaml, or PROD_CHECK_SECRETS_DIR
#    is set): each real secret file is a regular file of group 10010, mode 640 or stricter, in a
#    folder that gives the group and others nothing (mode 2700), and no host account has that group
#    (#511, #517; metadata only, no content);
# 4d. in an install folder, when the swag container is running (PROD_CHECK_SWAG_CONTAINER, default
#    `swag`) and web trusts PROXY_IP itself: PROXY_IP is one of that container's addresses (#500;
#    a typo inside Docker's range passes 4); not visible (no Docker daemon, no such container) is
#    a note, not a failure;
# 5. the runbook's `docker network create` keeps swag's fixed address outside Docker's --ip-range;
# 6. `nginx -t` accepts docs/runbooks/swag-classbeat.subdomain.conf, and the directives the app's
#    client address, body cap, SSE stream and headers rely on are in it. The file is read as nginx
#    reads it (directives and blocks, not lines, #500): X-Forwarded-For is set exactly once, in
#    the server, to $remote_addr, and no location sets a proxy_set_header or add_header;
# 6b. PROD_CHECK_SWAG_CONF=/path/to/the/real/file (runbook sections 7 and 9): the same rules and
#    `nginx -t` on the owner's live swag file, in either mode (with DNS only, the Cloudflare include
#    and real_ip_header may both be absent);
# 4e. in an install folder: with Turnstile on in the real render (the default since #792), TURNSTILE_SITE_KEY
#    must be set and secrets/turnstile_secret_key must not be empty (size only, never read), or web would
#    refuse to start; TURNSTILE_ENABLED=false skips it (#798, runbook sections 5b and 11);
# and a self-test for 4, 4b, 4c, 4d, 5 and 6 as for 2. On the server: run it in /opt/classbeat after writing
# .env (runbook section 9).
#
# What it is: a best-effort lint of the owner's own committed, reviewed compose files against honest
# mistakes. It is NOT a security boundary against someone who can edit those files (they could run
# anything). The boundary for the secrets is on the server: the files' mode and group (4c, runbook
# section 5) and which services mount which secret. Its compose rules are default-deny (#517): a key
# or a service the allow-list (check_keys) does not name is refused, so adding one to the production
# files needs a reviewed change to this script (runbook section 13). The swag rules read directives, but are the
# same kind of lint: they refuse the known ways to forge the client address, and nginx -t is the
# syntax check; the file is also read by swag, not by this script.
# Two places it runs (ADR 0020, #566):
#   a checkout (CI and developers): everything above, including the self-tests, the runbook and
#     the swag sample (needs docs/ and the nginx image);
#   an install folder (the released script next to compose.prod.yaml, no docs/): the pre-deploy
#     check on the server, `./check-prod-compose.sh [--probe]` from /opt/classbeat. It checks the
#     files there with dummy values (so a local edit of a compose file is caught), renders the real
#     .env, runs that real render (COMPOSE_FILE, every profile) through the same allow-list and
#     secret-mount rules (#610; the documented compose.bridge.yaml shape is allowed), checks the real
#     PROXY_IP, the env file and the real secret files and their folder, and
#     with --probe runs, per service that mounts secrets, a readability test of every secret file
#     from the pulled image (prints "<service>: <file> OK|FAIL", never a value; needs
#     `docker compose pull` first). `--install` forces that mode in a checkout.
set -euo pipefail
export MSYS_NO_PATHCONV=1   # Git Bash: paths below are for the container, never converted

mode=auto; probe=0; rollback_old=""
while (( $# )); do
  case "$1" in
    --install) mode=install ;;
    --probe) mode=install; probe=1 ;;
    --rollback-check) mode=install; shift; rollback_old="${1:-}"
      [[ "$rollback_old" =~ ^[0-9A-Za-z][0-9A-Za-z.+-]*$ ]] \
        || { echo "usage: check-prod-compose.sh --rollback-check OLD_VERSION (e.g. 1.2.3)" >&2; exit 2; } ;;
    *) echo "usage: check-prod-compose.sh [--install] [--probe] [--rollback-check OLD_VERSION]" >&2; exit 2 ;;
  esac
  shift
done
# The owner's own swag site file (runbook sections 7 and 9), checked with the same rules as the sample
# (#500). Made absolute here, before the script changes directory.
swag_live="${PROD_CHECK_SWAG_CONF:-}"
if [[ -n "$swag_live" ]]; then
  [[ "$swag_live" == /* || "$swag_live" =~ ^[A-Za-z]:[/\\] ]] || swag_live="$PWD/$swag_live"
  [[ -f "$swag_live" && -r "$swag_live" ]] \
    || { echo "check-prod-compose: PROD_CHECK_SWAG_CONF=$swag_live is not a readable file (copy it somewhere you can read, runbook section 9)" >&2; exit 1; }
fi
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# A checkout has scripts/release-assets.sh next to this script; the released install folder never does
# (it is not an asset). The test needs it, or a parent folder that happens to hold a compose.prod.yaml
# and docs/runbooks would make an install folder inside it look like a checkout (#470, review of #575).
if [[ -f "$here/release-assets.sh" && -f "$here/../compose.prod.yaml" && -d "$here/../docs/runbooks" ]]; then
  root="$(cd "$here/.." && pwd)"; layout=checkout
else
  root="$here"; layout=install
fi
[[ "$mode" != auto ]] || { [[ "$layout" == install ]] && mode=install || mode=repo; }
[[ -f "$root/compose.prod.yaml" && -f "$root/compose.prod.loopback.yaml" && -f "$root/compose.prod.lan.yaml" ]] \
  || { echo "check-prod-compose: compose.prod.yaml, compose.prod.loopback.yaml and compose.prod.lan.yaml must be next to this script" >&2; exit 1; }
cd "$root"

# --- The parts (#763) -------------------------------------------------------------------------------
# This file holds the header, the arguments and the layout. Each check-prod-compose-<part>.bash is
# sourced into this shell, in the order the checks always ran, and holds one responsibility (its first
# lines say which). They sit beside this script: a checkout's scripts/ and the install folder alike
# (release-assets.sh ships them as assets), so the script alone, without them, is refused here.
parts=(rollback setup rules secrets selftest-compose selftest-yaml owner secretfiles turnstile runbook
  site site-selftest probe)
for p in "${parts[@]}"; do
  [[ -f "$here/check-prod-compose-$p.bash" ]] \
    || { echo "check-prod-compose: check-prod-compose-$p.bash must be next to this script (download every check-prod-compose* asset, runbook section 4)" >&2; exit 1; }
  [[ -s "$here/check-prod-compose-$p.bash" ]] \
    || { echo "check-prod-compose: check-prod-compose-$p.bash is empty (download every check-prod-compose* asset again, runbook section 4)" >&2; exit 1; }
done
# shellcheck source=check-prod-compose-rollback.bash
source "$here/check-prod-compose-rollback.bash"
# shellcheck source=check-prod-compose-setup.bash
source "$here/check-prod-compose-setup.bash"
# shellcheck source=check-prod-compose-rules.bash
source "$here/check-prod-compose-rules.bash"
# shellcheck source=check-prod-compose-secrets.bash
source "$here/check-prod-compose-secrets.bash"

if [[ $mode == repo ]]; then   # the self-tests and the checkout-only checks (ADR 0020)
  # shellcheck source=check-prod-compose-selftest-compose.bash
  source "$here/check-prod-compose-selftest-compose.bash"
  # shellcheck source=check-prod-compose-selftest-yaml.bash
  source "$here/check-prod-compose-selftest-yaml.bash"
fi

# shellcheck source=check-prod-compose-owner.bash
source "$here/check-prod-compose-owner.bash"
# shellcheck source=check-prod-compose-secretfiles.bash
source "$here/check-prod-compose-secretfiles.bash"
# shellcheck source=check-prod-compose-turnstile.bash
source "$here/check-prod-compose-turnstile.bash"
# shellcheck source=check-prod-compose-runbook.bash
source "$here/check-prod-compose-runbook.bash"
# shellcheck source=check-prod-compose-site.bash
source "$here/check-prod-compose-site.bash"
# shellcheck source=check-prod-compose-site-selftest.bash
source "$here/check-prod-compose-site-selftest.bash"

# The owner's own file (PROD_CHECK_SWAG_CONF, runbook sections 7 and 9): the same rules and nginx -t.
if [[ -n "$swag_live" ]]; then
  site_label="$swag_live"
  check_site "$swag_live" live || exit 1
  nginx_site_t "$swag_live" || exit 1
  owner_note="$owner_note; the swag site config $swag_live passes the directive rules and nginx -t"
fi

# shellcheck source=check-prod-compose-probe.bash
source "$here/check-prod-compose-probe.bash"

if [[ $mode == install ]]; then
  echo "install check (compose files with dummies, the real render on the allow-list, .env, secrets/): ok; $owner_note"
  exit 0
fi
echo "compose.prod.yaml (shared network, loopback and LAN, default-deny keys, variables and networks, OAuth secrets on web only, secret files by group 10010), runbook network, swag site config and self-tests: ok; $owner_note"
