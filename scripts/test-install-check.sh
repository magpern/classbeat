#!/usr/bin/env bash
# Tests the released pre-deploy check from an install folder (ADR 0020, #566): builds the release
# assets, lays them out like /opt/classbeat with a fake .env and secrets/, and runs the asset copy of
# check-prod-compose.sh there. Needs docker (the client and compose plugin only: no daemon, no pull),
# jq and the right to give files group 10010 (root or passwordless sudo; without it the secret-file
# cases are skipped with a message). --probe runs against a stub `docker` that answers `compose run`.
#   scripts/test-install-check.sh
set -euo pipefail
export MSYS_NO_PATHCONV=1

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
gid=10010
real_docker="$(command -v docker)"

as_root() { if [ "$(id -u)" = 0 ]; then "$@"; else sudo -n "$@"; fi; }
can_gid=1
if [ "$(id -u)" != 0 ] && ! sudo -n true 2>/dev/null; then can_gid=0; fi

fails=0
ok()   { echo "ok: $*"; }
bad()  { echo "FAIL: $*" >&2; fails=$((fails + 1)); }

"$root/scripts/release-assets.sh" build "$work/assets"
( cd "$work/assets" && sha256sum -c --quiet SHA256SUMS ) || bad "SHA256SUMS does not verify"
# The asset list is exactly what the runbook downloads.
want="SHA256SUMS backup.sh check-prod-compose-owner.bash check-prod-compose-probe.bash check-prod-compose-rollback.bash check-prod-compose-rules.bash check-prod-compose-runbook.bash check-prod-compose-secretfiles.bash check-prod-compose-secrets.bash check-prod-compose-selftest-compose.bash check-prod-compose-selftest-yaml.bash check-prod-compose-setup.bash check-prod-compose-site-selftest.bash check-prod-compose-site.bash check-prod-compose-turnstile.bash check-prod-compose.sh compose.prod.lan.yaml compose.prod.loopback.yaml compose.prod.yaml install-swag-logrotate.sh prod.env.example restore.sh swag-classbeat-logrotate.conf swag-classbeat.subdomain.conf validate-backup.sh"
# find, not a glob: a glob (`*`) would not list a dotfile, so a hidden asset (a .env) could never fail this.
list_dir() { find "$work/assets" -mindepth 1 -maxdepth 1 -printf '%f\n'; }
got="$(list_dir | LC_ALL=C sort | tr '\n' ' ' | sed 's/ $//')"
if [ "$got" = "$want" ]; then ok "asset list"; else bad "asset list is '$got', want '$want'"; fi
# SHA256SUMS names every other asset, none twice (dotfiles included, were there any).
sums="$(awk '{print $2}' "$work/assets/SHA256SUMS" | LC_ALL=C sort | tr '\n' ' ' | sed 's/ $//')"
others="$(list_dir | grep -vx SHA256SUMS | LC_ALL=C sort | tr '\n' ' ' | sed 's/ $//')"
if [ "$sums" = "$others" ]; then ok "SHA256SUMS lists every asset"; else bad "SHA256SUMS lists '$sums', the assets are '$others'"; fi
# A hidden asset is refused by the build (its glob and sha256sum would skip it). Run on a copy of the
# script in a scratch tree that holds the asset sources, with one extra dotfile entry.
fake="$work/fake-root"; mkdir -p "$fake/scripts"
while read -r src _; do mkdir -p "$fake/$(dirname "$src")"; cp "$root/$src" "$fake/$src"; done < <("$root/scripts/release-assets.sh" list)
sed 's|^  "scripts/backup.sh backup.sh"$|&\n  "scripts/backup.sh .hidden"|' "$root/scripts/release-assets.sh" > "$fake/scripts/release-assets.sh"
chmod +x "$fake/scripts/release-assets.sh"
if ! grep -q '\.hidden' "$fake/scripts/release-assets.sh"; then bad "hidden-asset test: the scratch script was not edited"
elif out="$("$fake/scripts/release-assets.sh" build "$work/fake-assets" 2>&1)"; then bad "a dotfile asset was built"
elif grep -q 'must not start with a dot' <<<"$out"; then ok "a hidden asset is refused"
else bad "hidden asset refused for another reason: $out"; fi
# The runbook downloads exactly these files, in section 3 and again in section 11.
runbook="$root/docs/runbooks/vps-deploy.md"
if [ -f "$runbook" ]; then
  names="$(printf '%s ' "$(list_dir)" | tr -s ' \n' ' ' | sed 's/ $//')"
  mapfile -t loops < <(awk '/^(  )?for f in /{buf=$0; while (buf ~ /\\$/) { getline nxt; sub(/\\$/, "", buf); buf = buf nxt } print buf}' "$runbook")
  if [ "${#loops[@]}" -ne 2 ]; then bad "the runbook has ${#loops[@]} download loops, want 2 (sections 3 and 11)"; fi
  for l in "${loops[@]}"; do
    l="${l#*for f in }"; l="${l%%; do*}"
    l="$(tr ' ' '\n' <<<"$l" | grep -v '^$' | sort | tr '\n' ' ' | sed 's/ $//')"
    if [ "$l" = "$(tr ' ' '\n' <<<"$names" | sort | tr '\n' ' ' | sed 's/ $//')" ]; then ok "runbook download loop"; else bad "a runbook download loop differs from the assets: $l"; fi
  done
fi
# No asset may be a secret holder: nothing but committed text files, none under secrets/ or named .env.
if grep -rIlE -- '-----BEGIN [A-Z ]*PRIVATE KEY|swedebeat_app\.json' "$work/assets" >/dev/null; then bad "an asset mentions a key or the app key file"; fi

# The log-rotation installer (#826): against a stub docker it writes the shipped rule to swag's
# volume path, dry-runs it, schedules nothing, and --check writes nothing.
lr="$work/logrotate"; mkdir -p "$lr/bin"
cat > "$lr/bin/docker" <<'STUB'
#!/usr/bin/env bash
echo "docker $*" >> "$LR_LOG"
case "$*" in *"cat >"*) cat > "$LR_WRITTEN" ;; esac
STUB
chmod +x "$lr/bin/docker"
export LR_LOG="$lr/log" LR_WRITTEN="$lr/written"
if PATH="$lr/bin:$PATH" "$work/assets/install-swag-logrotate.sh" >/dev/null 2>&1 \
  && cmp -s "$lr/written" "$work/assets/swag-classbeat-logrotate.conf" \
  && grep -q 'logrotate -d /config/log/classbeat-logrotate.conf' "$lr/log" \
  && ! grep -qE 'cron|systemctl' "$lr/log"; then ok "logrotate installer writes the rule and dry-runs it"
else bad "logrotate installer did not write the shipped rule and dry-run it"; fi
rm -f "$lr/written" "$lr/log"
if PATH="$lr/bin:$PATH" "$work/assets/install-swag-logrotate.sh" --check >/dev/null 2>&1 \
  && [ ! -e "$lr/written" ] && grep -q 'logrotate -d' "$lr/log"; then ok "logrotate installer --check only dry-runs"
else bad "logrotate installer --check wrote or did not dry-run"; fi

install="$work/install"
mkdir "$install"
cp "$work/assets"/* "$install/"
cp "$install/prod.env.example" "$install/.env"
sed -i -e 's/^CLASSBEAT_VERSION=.*/CLASSBEAT_VERSION=1.2.3/' "$install/.env"
# Turnstile is on by default (#792): a good install has the public site key (a placeholder) and, below, a secret file.
echo 'TURNSTILE_SITE_KEY=0x4AAAAAAcheck' >> "$install/.env"
names=(postgres_password database_owner_password database_password database_maint_password app_secret_key
  connection_kek smtp_password anthropic_api_key openai_api_key deepseek_api_key swedebeat_app
  oauth_google_client_secret oauth_github_client_secret oauth_microsoft_client_secret turnstile_secret_key)
canary="secret-canary-$RANDOM$RANDOM$RANDOM"
mksecrets() {
  rm -rf "$install/secrets"; mkdir -m 2700 "$install/secrets"
  # Each file holds a canary (never a real secret): no output of the check may ever contain it.
  local n; for n in "${names[@]}"; do install -m 640 /dev/null "$install/secrets/$n"; printf '%s-%s' "$canary" "$n" > "$install/secrets/$n"; done
  if (( can_gid )); then as_root chgrp "$gid" "$install/secrets" "$install"/secrets/*; as_root chmod 2700 "$install/secrets"; fi
}
run_check() { ( cd "$install" && ./check-prod-compose.sh "$@" 2>&1 ); }
expect() {   # expect NAME WANT_RC PATTERN [args...]: exit status and a pattern in the output
  local name="$1" want="$2" pat="$3" out rc=0
  shift 3
  out="$(run_check "$@")" || rc=$?
  if grep -q -- "$canary" <<<"$out"; then bad "$name: the output holds the content of a secret file"; return; fi
  if [ "$rc" = "$want" ] && grep -Eq -- "$pat" <<<"$out"; then ok "$name"; else bad "$name (rc $rc, want $want; pattern '$pat'): $(tail -3 <<<"$out")"; fi
}

# 1. Settings problems, whatever the secret files are.
mv "$install/.env" "$install/.env.keep"
expect "no .env" 1 'no .*\.env: cp prod\.env\.example'
mv "$install/.env.keep" "$install/.env"
mksecrets
cp "$install/compose.prod.yaml" "$work/compose.prod.yaml.good"
sed -i 's|^    user: postgres$|&\n    ports: ["5432:5432"]|' "$install/compose.prod.yaml"
expect "a local edit publishing db's port is caught" 1 'only web may publish a port|db must not publish ports'

cp "$work/compose.prod.yaml.good" "$install/compose.prod.yaml"
# Default deny (#517): a key or a file reference the allow-list does not name is caught in the folder.
sed -i 's|^    user: postgres$|&\n    privileged: true|' "$install/compose.prod.yaml"
expect "a local edit adding privileged is caught" 1 'not on the allow-list.*services\.db\.privileged'
cp "$work/compose.prod.yaml.good" "$install/compose.prod.yaml"
sed -i 's|^    user: postgres$|&\n    env_file: ./secrets/app_secret_key|' "$install/compose.prod.yaml"
expect "a local edit adding env_file is caught" 1 'services\.db\.environment: 1 variable|services\.db-functions\.environment'   # the render, before the text lint
cp "$work/compose.prod.yaml.good" "$install/compose.prod.yaml"
# The text rule's bypass (#610): an explicit `? env_file` key gets past the line match; the renders (the
# dummy one, whose secret files hold a line, and the real one) do not.
sed -i '0,/^    user: postgres$/s||&\n    ? env_file\n    : ./secrets/app_secret_key|' "$install/compose.prod.yaml"   # db only
expect "a local edit adding '? env_file' is caught by the render" 1 'services\.db\.environment: 1 variable'
cp "$work/compose.prod.yaml.good" "$install/compose.prod.yaml"
# The real render (#610): an overlay of the owner's own in COMPOSE_FILE goes through the allow-list too.
# Each case is refused by the real render alone (the dummy render never sees compose.extra.yaml), and the
# secret file's content (a canary) never shows in the output (expect checks it).
cp "$install/.env" "$work/env.before-real"
sed -i 's/^COMPOSE_FILE=.*/COMPOSE_FILE=compose.prod.yaml:compose.extra.yaml/' "$install/.env"
printf 'services:\n  web:\n    "env_file": ./secrets/app_secret_key\n' > "$install/compose.extra.yaml"
expect "real render: a quoted env_file of a secret file" 1 'services\.web\.environment: 1 variable'
printf 'services:\n  worker:\n    ? env_file\n    : ./secrets/connection_kek\n' > "$install/compose.extra.yaml"
expect "real render: an explicit env_file key" 1 'services\.worker\.environment: 1 variable'
printf 'services:\n  spy:\n    image: busybox\n    profiles: [ops]\n' > "$install/compose.extra.yaml"
expect "real render: a profiled service" 1 'not on the allow-list.*services\.spy'
printf 'services:\n  web:\n    networks: [spynet]\nnetworks:\n  spynet:\n    external: true\n    name: other\n' > "$install/compose.extra.yaml"
expect "real render: a network of the owner's own" 1 'networks\.spynet'
# A secret file that does not parse as an env file (#616 review): Compose's error quotes the whole line,
# so it must never be printed. `expect` checks stdout and stderr for the canary; the fixed sentence is all.
printf 'a~%s' "$canary" > "$install/secrets/oauth_microsoft_client_secret"
printf '{"k":"%s"}' "$canary" > "$install/secrets/swedebeat_app"
for f in oauth_microsoft_client_secret swedebeat_app; do
  printf 'services:\n  web:\n    env_file: ./secrets/%s\n' "$f" > "$install/compose.extra.yaml"
  expect "real render: an env_file of a secret that does not parse ($f) prints no Compose output" 1 \
    "docker compose config fails with your \\.env: .*Compose's message is not printed"
done
# The same through the dummy render alone: a local edit of the LAN overlay (not in COMPOSE_FILE, so the
# real render passes) naming the real file by its path.
cp "$install/.env" "$work/env.parse"; cp "$work/env.before-real" "$install/.env"
cp "$install/compose.prod.lan.yaml" "$work/compose.prod.lan.yaml.good"
sed -i "s|^  web:\$|&\n    env_file: $install/secrets/oauth_microsoft_client_secret|" "$install/compose.prod.lan.yaml"
expect "dummy render: an env_file of a secret that does not parse prints no Compose output" 1 \
  "fails on the compose files with dummy values \\(Compose's message is not printed"
cp "$work/compose.prod.lan.yaml.good" "$install/compose.prod.lan.yaml"; cp "$work/env.parse" "$install/.env"
mksecrets
mkdir -p "$install/smtp-ca"; : > "$install/smtp-ca/bridge.pem"
bridge='services:\n  web:\n    volumes:\n      - ./smtp-ca/bridge.pem:/run/smtp-ca/bridge.pem%s\n    networks:\n      - bridge-mail\nnetworks:\n  bridge-mail:\n    external: true\n    name: proton-bridge-net\n'
# shellcheck disable=SC2059  # the format is ours
printf "$bridge" '' > "$install/compose.bridge.yaml"
sed -i 's/^COMPOSE_FILE=.*/COMPOSE_FILE=compose.prod.yaml:compose.bridge.yaml/' "$install/.env"
expect "real render: the bridge overlay writable" 1 'services\.web\.volumes'
# shellcheck disable=SC2059  # the format is ours
printf "$bridge" ':ro' > "$install/compose.bridge.yaml"
cp "$install/.env" "$work/env.bridge"
rm -f "$install/compose.extra.yaml"
cp "$work/env.before-real" "$install/.env"
sed -i 's/^PROXY_IP=.*/PROXY_IP=8.8.8.8/' "$install/.env"
expect "a public PROXY_IP" 1 'PROXY_IP must be one private'
sed -i 's/^PROXY_IP=.*/PROXY_IP=172.29.90.10/' "$install/.env"
echo 'OAUTH_GOOGLE_CLIENT_SECRET=x' >> "$install/.env"
expect "a client secret in .env" 1 'CLIENT_SECRET'
sed -i '/CLIENT_SECRET/d' "$install/.env"

# 1a. A parent folder that holds compose.prod.yaml and docs/runbooks is not a checkout: the script
# next to the install folder's files is an install, whatever the folder above it holds (#575 review).
parent="$work/parent"; mkdir -p "$parent/docs/runbooks" "$parent/inst"
cp "$install/compose.prod.yaml" "$parent/"
cp "$install"/check-prod-compose*.sh "$install"/check-prod-compose-*.bash "$install"/compose.prod.*.yaml "$install"/compose.prod.yaml "$parent/inst/"
out="$(cd "$parent/inst" && ./check-prod-compose.sh 2>&1)" && rc=0 || rc=$?
if [ "$rc" = 1 ] && grep -Eq 'no .*\.env: cp prod\.env\.example' <<<"$out"; then ok "a parent folder with compose.prod.yaml and docs/runbooks is still an install"
else bad "parent-folder layout (rc $rc): $(tail -2 <<<"$out")"; fi

# 1b. Where web is published (real values): the loopback and LAN overlays.
cp "$install/.env" "$work/env.good"
printf 'COMPOSE_FILE=compose.prod.yaml:compose.prod.lan.yaml\nWEB_LAN_ADDRESS=0.0.0.0\n' >> "$install/.env"
expect "LAN overlay on every interface" 1 'web is published on .0\.0\.0\.0'
sed -i 's/^WEB_LAN_ADDRESS=.*/WEB_LAN_ADDRESS=8.8.8.8/' "$install/.env"
expect "LAN overlay on a public address" 1 'only 127\.0\.0\.1 or one private LAN address'
sed -i 's/^WEB_LAN_ADDRESS=.*/WEB_LAN_ADDRESS=172.29.90.10/' "$install/.env"
expect "swag's address is this server's" 1 'PROXY_IP .* is this server'
# Both shipped overlays together publish web twice (loopback and LAN): only one overlay at a time.
sed -i 's/^COMPOSE_FILE=.*/COMPOSE_FILE=compose.prod.yaml:compose.prod.loopback.yaml:compose.prod.lan.yaml/; s/^WEB_LAN_ADDRESS=.*/WEB_LAN_ADDRESS=192.168.1.20/' "$install/.env"
expect "both overlays together" 1 'web publishes at most one port'
sed -i 's/^COMPOSE_FILE=.*/COMPOSE_FILE=compose.prod.yaml:compose.prod.lan.yaml/' "$install/.env"
# A second port from an overlay of the owner's own, next to the LAN one.
printf 'services:\n  web:\n    ports:\n      - "192.168.1.20:8081:8000"\n' > "$install/compose.extra.yaml"
sed -i 's/^COMPOSE_FILE=.*/COMPOSE_FILE=compose.prod.yaml:compose.prod.lan.yaml:compose.extra.yaml/' "$install/.env"
expect "a second port from an own overlay" 1 'web publishes at most one port'
rm -f "$install/compose.extra.yaml"
sed -i 's/^COMPOSE_FILE=.*/COMPOSE_FILE=compose.prod.yaml:compose.prod.lan.yaml/' "$install/.env"
sed -i 's/^WEB_LAN_ADDRESS=.*/WEB_LAN_ADDRESS=192.168.1.20/; s/^PROXY_IP=.*/PROXY_IP=192.168.1.10/' "$install/.env"
sed -i 's/^COMPOSE_FILE=compose.prod.yaml$//' "$install/.env"
cp "$install/.env" "$work/env.lan"
cp "$work/env.good" "$install/.env"

# 2. The secret files (needs group 10010).
if (( can_gid )); then
  expect "good install" 0 '^install check'
  cp "$work/env.lan" "$install/.env"
  expect "good LAN install" 0 '^install check'
  cp "$work/env.bridge" "$install/.env"
  expect "good install with the documented compose.bridge.yaml (#610)" 0 '^install check'
  cp "$work/env.good" "$install/.env"
  # 2b. The owner's own swag file (PROD_CHECK_SWAG_CONF, #500). The directive rules run before
  # nginx -t, so a refused file needs no Docker daemon here.
  # shellcheck disable=SC2016  # a literal nginx variable
  sed -E 's/^( *proxy_set_header X-Forwarded-Proto https;)$/\1 proxy_set_header X-Forwarded-For $http_x_forwarded_for;/' \
    "$install/swag-classbeat.subdomain.conf" > "$work/swag-bad.conf"
  PROD_CHECK_SWAG_CONF="$work/swag-bad.conf" expect "swag file with a second X-Forwarded-For" 1 \
    'exactly one proxy_set_header X-Forwarded-For \(found 2\)'
  PROD_CHECK_SWAG_CONF="$work/swag-missing.conf" expect "swag file that is not there" 1 'is not a readable file'
  chmod 644 "$install/secrets/connection_kek"
  expect "mode 644" 1 'chmod 640 secrets/connection_kek'
  mksecrets; chmod 600 "$install/secrets/app_secret_key"
  expect "mode 600" 1 'group must read it: chmod 640'
  mksecrets; as_root chgrp 0 "$install/secrets/smtp_password"
  expect "file group" 1 'sudo chgrp 10010 secrets/smtp_password'
  mksecrets; as_root chgrp 0 "$install/secrets"
  expect "folder group says sudo" 1 'One-time, needs sudo: sudo chgrp 10010 '
  mksecrets; as_root chmod g-s "$install/secrets"
  expect "folder without setgid" 1 'no setgid bit.*sudo chmod 2700'
  mksecrets; chmod 2750 "$install/secrets"
  expect "folder open to the group" 1 'gives the group or others access'
  mksecrets; rm "$install/secrets/openai_api_key"
  expect "missing file" 1 'openai_api_key is missing'
  # 2c. Turnstile on (the default) needs its keys (#798); the secret file is only size-tested, and
  # `expect` fails if a canary (the other files' content, or this one's) shows in the output.
  mksecrets; : > "$install/secrets/turnstile_secret_key"
  expect "Turnstile on, empty secret file" 1 'turnstile_secret_key is missing or empty.*section 5b.*TURNSTILE_ENABLED=false'
  mksecrets; cp "$install/.env" "$work/env.ts"; sed -i '/^TURNSTILE_SITE_KEY=/d' "$install/.env"
  expect "Turnstile on, no site key" 1 'TURNSTILE_SITE_KEY in .env is empty.*section 5b.*TURNSTILE_ENABLED=false'
  echo 'TURNSTILE_SITE_KEY=' >> "$install/.env"
  expect "Turnstile on, empty site key" 1 'TURNSTILE_SITE_KEY in .env is empty'
  echo 'TURNSTILE_ENABLED=false' >> "$install/.env"
  : > "$install/secrets/turnstile_secret_key"
  expect "Turnstile off, no keys" 0 '^install check'
  mksecrets; cp "$work/env.ts" "$install/.env"; echo 'TURNSTILE_ENABLED=true' >> "$install/.env"
  expect "Turnstile on explicitly, both keys present" 0 '^install check'
  cp "$work/env.ts" "$install/.env"
  mksecrets
else
  echo "skip: no root or passwordless sudo: the secret-file cases need group $gid"
fi

# 3. --probe against a stub docker: `compose run` answers a canned listing, anything else is real.
stub="$work/bin"; mkdir "$stub"
cat > "$stub/docker" <<STUB
#!/usr/bin/env bash
if [ "\${1:-}" = compose ] && [[ " \$* " == *" exec "* ]]; then
  printf '%s' "\${DB_REV-}"; exit \${DB_RC:-0}
fi
if [ "\${1:-}" = inspect ]; then
  printf '%s' "\${SWAG_OUT-}"; exit 0
fi
if [ "\${1:-}" = run ]; then
  # The rollback check's ls of the old image needs no network (#591): refuse it without the flag.
  if [[ " \$* " == *" --entrypoint ls "* ]]; then
    printf '%s\\n' "\$*" >> "$work/ls-run.args"
    [[ " \$* " == *" --network none "* ]] || { echo "stub: docker run ls without --network none" >&2; exit 97; }
  fi
  printf '%s' "\${REVS_OUT-}"; exit \${REVS_RC:-0}
fi
if [ "\${1:-}" = compose ] && [[ " \$* " == *" run "* ]]; then
  if [ -n "\${PROBE_REAL-}" ]; then
    # Runs the check's own probe script (the argument after -c) on this folder's secrets/.
    script=""; prev=""
    for a in "\$@"; do if [ "\$prev" = -c ]; then script="\$a"; fi; prev="\$a"; done
    exec sh -c "\${script//\/run\/secrets/\$PROBE_REAL}"
  fi
  printf '%s' "\${PROBE_OUT-}"
  exit \${PROBE_RC:-0}
fi
exec "$real_docker" "\$@"
STUB
chmod +x "$stub/docker"
if (( can_gid )); then
  if PATH="$stub:$PATH" PROBE_OUT=$'app_secret_key OK\nconnection_kek OK\n' \
    bash -c 'cd "$1" && ./check-prod-compose.sh --probe' _ "$install" >"$work/probe-ok.log" 2>&1 \
    && grep -q '^web: app_secret_key OK' "$work/probe-ok.log"; then
    ok "probe: all OK"
  else bad "probe: all OK"; tail -3 "$work/probe-ok.log" >&2; fi
  if PATH="$stub:$PATH" PROBE_OUT=$'app_secret_key OK\nconnection_kek FAIL\n' \
    bash -c 'cd "$1" && ./check-prod-compose.sh --probe' _ "$install" >"$work/probe-fail.log" 2>&1; then
    bad "probe: a FAIL line passed"
  elif grep -q 'a secret file is not readable' "$work/probe-fail.log"; then ok "probe: FAIL"; else bad "probe: FAIL message"; fi
  if PATH="$stub:$PATH" PROBE_OUT='' \
    bash -c 'cd "$1" && ./check-prod-compose.sh --probe' _ "$install" >"$work/probe-none.log" 2>&1; then
    bad "probe: no output passed"
  elif grep -q 'listed no secret file' "$work/probe-none.log"; then ok "probe: no output"; else bad "probe: no-output message"; fi
  if PATH="$stub:$PATH" PROBE_OUT='whatever' PROBE_RC=1 \
    bash -c 'cd "$1" && ./check-prod-compose.sh --probe' _ "$install" >"$work/probe-rc.log" 2>&1; then
    bad "probe: a failing docker run passed"
  else ok "probe: docker run fails"; fi
  # The probe script itself (not a canned answer): it lists every secret file and prints no content.
  # The files hold a canary, so a script that printed them (cat, head) would be caught here.
  if PATH="$stub:$PATH" PROBE_REAL="$install/secrets" \
    bash -c 'cd "$1" && ./check-prod-compose.sh --probe' _ "$install" >"$work/probe-real.log" 2>&1 \
    && grep -q '^web: app_secret_key OK' "$work/probe-real.log" \
    && [ "$(grep -c ': .* OK$' "$work/probe-real.log")" -ge "${#names[@]}" ] \
    && ! grep -q -- "$canary" "$work/probe-real.log" && ! grep -q 'FAIL' "$work/probe-real.log"; then
    ok "probe: the real script lists the secret files and prints no content"
  else bad "probe: the real script"; tail -3 "$work/probe-real.log" | cut -c1-120 >&2; fi
fi

# 3b. PROXY_IP against the running swag container's address (stub `docker inspect`, #500).
if (( can_gid )); then
  swag_run() { ( cd "$install" && PATH="$stub:$PATH" SWAG_OUT="$1" ./check-prod-compose.sh --install 2>&1 ); }
  if out="$(swag_run '172.29.90.10 ')" && grep -q "PROXY_IP is the running swag container's address" <<<"$out"; then
    ok "swag address: PROXY_IP matches"; else bad "swag address: PROXY_IP matches: $(tail -2 <<<"$out")"; fi
  if out="$(swag_run '172.29.90.130 ')"; then bad "swag address: a different PROXY_IP passed"
  elif grep -q 'PROXY_IP is 172.29.90.10 but the running swag container has 172.29.90.130' <<<"$out"; then ok "swag address: mismatch"
  else bad "swag address: mismatch message: $(tail -2 <<<"$out")"; fi
  if out="$(swag_run '')" && grep -q 'swag container is not visible here' <<<"$out"; then
    ok "swag address: not visible is a note, not a failure"; else bad "swag address: not visible: $(tail -2 <<<"$out")"; fi
fi

# 3c. nginx -t failing on the owner's swag file (stub `docker run`, #470): only the bounded summary is
# printed, never nginx's log, whose quoted tokens can come from the live file.
if (( can_gid )); then
  canary="CANARY-$RANDOM$RANDOM-swag"
  { cat "$install/swag-classbeat.subdomain.conf"; printf '# %s\n' "$canary"; } > "$work/swag-canary.conf"
  rc=0
  out="$(cd "$install" && PATH="$stub:$PATH" PROD_CHECK_SWAG_CONF="$work/swag-canary.conf" REVS_RC=1 \
    REVS_OUT="nginx: [emerg] unknown directive \"$canary\" in /config/nginx/proxy-confs/classbeat.subdomain.conf:7
$canary" ./check-prod-compose.sh --install 2>&1)" || rc=$?
  if [ "$rc" = 1 ] && grep -q 'nginx -t: emerg at line 7 of the site config' <<<"$out" \
    && grep -q 'nginx -t failed' <<<"$out" && ! grep -qF -- "$canary" <<<"$out"; then
    ok "nginx -t failure prints only the redacted summary"
  else bad "nginx -t failure summary (rc $rc): $(grep -c -F -- "$canary" <<<"$out") canary line(s)"; fi
fi

# 4. --rollback-check OLD: the database's revision must be one OLD's image carries (stub docker).
rb() {   # rb NAME WANT_RC PATTERN DB_REV REVS_OUT [DB_RC REVS_RC]
  local name="$1" want="$2" pat="$3" out rc=0
  out="$(cd "$install" && PATH="$stub:$PATH" DB_REV="$4" REVS_OUT="$5" DB_RC="${6:-0}" REVS_RC="${7:-0}" \
    ./check-prod-compose.sh --rollback-check 1.2.2 2>&1)" || rc=$?
  if [ "$rc" = "$want" ] && grep -Eq -- "$pat" <<<"$out"; then ok "$name"; else bad "$name (rc $rc, want $want): $(tail -2 <<<"$out")"; fi
}
old_files=$'0001_baseline.py\n0025_running_flag.py\n0026_plan_gates.py\n__pycache__\n'
rb "rollback: revision known to OLD" 0 'every database revision is known' $'0026_plan_gates\n' "$old_files"
rb "rollback: revision newer than OLD" 1 'revision 0027_new, which release 1.2.2 does not have' $'0027_new\n' "$old_files"
rb "rollback: one of two unknown" 1 '0027_other' $'0026_plan_gates\n0027_other\n' "$old_files"
rb "rollback: a prefix is not a match" 1 '0026_plan' $'0026_plan\n' "$old_files"
rb "rollback: empty alembic_version" 1 'no Alembic revision' '' "$old_files"
rb "rollback: odd revision text" 1 'odd revision' $'0026; drop\n' "$old_files"
rb "rollback: db not reachable" 1 'cannot read the database revision' '' "$old_files" 1
rb "rollback: image not pulled" 1 'cannot list the migrations' $'0026_plan_gates\n' '' 0 1
if [ -s "$work/ls-run.args" ] && ! grep -qv -- '--network none' "$work/ls-run.args"; then
  ok "rollback: the old image's ls runs with --network none"
else bad "rollback: the old image's ls ran without --network none: $(tail -1 "$work/ls-run.args" 2>&1)"; fi
if ( cd "$install" && ./check-prod-compose.sh --rollback-check >/dev/null 2>&1 ); then bad "rollback: no version given passed"; else ok "rollback: needs a version"; fi

# The script is split into sourced parts (#763) that travel with it: without one, it refuses to run
# (a download of the script alone must not run a partial set of checks), and every part in scripts/ is
# one the script sources and the release ships.
miss="$work/missing-part"; mkdir "$miss"; cp "$install"/check-prod-compose* "$install"/compose.prod*.yaml "$miss/"
rm "$miss/check-prod-compose-owner.bash"
out="$(cd "$miss" && ./check-prod-compose.sh 2>&1)" && rc=0 || rc=$?
if [ "$rc" = 1 ] && grep -q 'check-prod-compose-owner.bash must be next to this script' <<<"$out"; then ok "a missing part is refused"
else bad "a missing part (rc $rc): $(tail -2 <<<"$out")"; fi
: >"$miss/check-prod-compose-owner.bash"
out="$(cd "$miss" && ./check-prod-compose.sh 2>&1)" && rc=0 || rc=$?
if [ "$rc" = 1 ] && grep -q 'check-prod-compose-owner.bash is empty' <<<"$out"; then ok "an empty part is refused"
else bad "an empty part (rc $rc): $(tail -2 <<<"$out")"; fi
# shellcheck disable=SC2016  # a literal `$here` in the pattern, not a shell expansion
srcd="$(grep -o 'source "$here/check-prod-compose-[a-z-]*\.bash"' "$root/scripts/check-prod-compose.sh" | sed 's/.*compose-//; s/\.bash"//' | LC_ALL=C sort | tr '\n' ' ')"
have="$(cd "$root/scripts" && printf '%s\n' check-prod-compose-*.bash | sed 's/check-prod-compose-//; s/\.bash$//' | LC_ALL=C sort | tr '\n' ' ')"
if [ -n "$srcd" ] && [ "$srcd" = "$have" ]; then ok "every part is sourced"; else bad "parts sourced '$srcd' differ from the files '$have'"; fi

(( fails == 0 )) || { echo "test-install-check: $fails failure(s)" >&2; exit 1; }
echo "test-install-check: ok"
