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
want="SHA256SUMS backup.sh check-prod-compose.sh compose.prod.lan.yaml compose.prod.loopback.yaml compose.prod.yaml prod.env.example restore.sh swag-classbeat.subdomain.conf validate-backup.sh"
got="$(cd "$work/assets" && printf '%s\n' * | tr '\n' ' ' | sed 's/ $//')"
if [ "$got" = "$want" ]; then ok "asset list"; else bad "asset list is '$got', want '$want'"; fi
# The runbook downloads exactly these files, in section 3 and again in section 11.
runbook="$root/docs/runbooks/vps-deploy.md"
if [ -f "$runbook" ]; then
  names="$(printf '%s ' "$(cd "$work/assets" && printf '%s\n' *)" | tr -s ' \n' ' ' | sed 's/ $//')"
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

install="$work/install"
mkdir "$install"
cp "$work/assets"/* "$install/"
cp "$install/prod.env.example" "$install/.env"
sed -i -e 's/^CLASSBEAT_VERSION=.*/CLASSBEAT_VERSION=1.2.3/' "$install/.env"
names=(postgres_password database_owner_password database_password database_maint_password app_secret_key
  connection_kek smtp_password anthropic_api_key openai_api_key deepseek_api_key swedebeat_app
  oauth_google_client_secret oauth_github_client_secret oauth_microsoft_client_secret)
mksecrets() {
  rm -rf "$install/secrets"; mkdir -m 2700 "$install/secrets"
  local n; for n in "${names[@]}"; do install -m 640 /dev/null "$install/secrets/$n"; done
  if (( can_gid )); then as_root chgrp "$gid" "$install/secrets" "$install"/secrets/*; as_root chmod 2700 "$install/secrets"; fi
}
run_check() { ( cd "$install" && ./check-prod-compose.sh "$@" 2>&1 ); }
expect() {   # expect NAME WANT_RC PATTERN [args...]: exit status and a pattern in the output
  local name="$1" want="$2" pat="$3" out rc=0
  shift 3
  out="$(run_check "$@")" || rc=$?
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
expect "a local edit adding env_file is caught" 1 'no env_file, label_file, profiles'
cp "$work/compose.prod.yaml.good" "$install/compose.prod.yaml"
sed -i 's/^PROXY_IP=.*/PROXY_IP=8.8.8.8/' "$install/.env"
expect "a public PROXY_IP" 1 'PROXY_IP must be one private'
sed -i 's/^PROXY_IP=.*/PROXY_IP=172.29.90.10/' "$install/.env"
echo 'OAUTH_GOOGLE_CLIENT_SECRET=x' >> "$install/.env"
expect "a client secret in .env" 1 'CLIENT_SECRET'
sed -i '/CLIENT_SECRET/d' "$install/.env"

# 1b. Where web is published (real values): the loopback and LAN overlays.
cp "$install/.env" "$work/env.good"
printf 'COMPOSE_FILE=compose.prod.yaml:compose.prod.lan.yaml\nWEB_LAN_ADDRESS=0.0.0.0\n' >> "$install/.env"
expect "LAN overlay on every interface" 1 'web is published on .0\.0\.0\.0'
sed -i 's/^WEB_LAN_ADDRESS=.*/WEB_LAN_ADDRESS=8.8.8.8/' "$install/.env"
expect "LAN overlay on a public address" 1 'only 127\.0\.0\.1 or one private LAN address'
sed -i 's/^WEB_LAN_ADDRESS=.*/WEB_LAN_ADDRESS=172.29.90.10/' "$install/.env"
expect "swag's address is this server's" 1 'PROXY_IP .* is this server'
sed -i 's/^WEB_LAN_ADDRESS=.*/WEB_LAN_ADDRESS=192.168.1.20/; s/^PROXY_IP=.*/PROXY_IP=192.168.1.10/' "$install/.env"
sed -i 's/^COMPOSE_FILE=compose.prod.yaml$//' "$install/.env"
cp "$install/.env" "$work/env.lan"
cp "$work/env.good" "$install/.env"

# 2. The secret files (needs group 10010).
if (( can_gid )); then
  expect "good install" 0 '^install check'
  cp "$work/env.lan" "$install/.env"
  expect "good LAN install" 0 '^install check'
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
  printf '%s' "\${REVS_OUT-}"; exit \${REVS_RC:-0}
fi
if [ "\${1:-}" = compose ] && [[ " \$* " == *" run "* ]]; then
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
if ( cd "$install" && ./check-prod-compose.sh --rollback-check >/dev/null 2>&1 ); then bad "rollback: no version given passed"; else ok "rollback: needs a version"; fi

(( fails == 0 )) || { echo "test-install-check: $fails failure(s)" >&2; exit 1; }
echo "test-install-check: ok"
