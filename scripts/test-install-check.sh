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
want="SHA256SUMS check-prod-compose.sh compose.prod.loopback.yaml compose.prod.yaml prod.env.example swag-classbeat.subdomain.conf"
got="$(cd "$work/assets" && printf '%s\n' * | tr '\n' ' ' | sed 's/ $//')"
if [ "$got" = "$want" ]; then ok "asset list"; else bad "asset list is '$got', want '$want'"; fi
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
sed -i 's/^PROXY_IP=.*/PROXY_IP=8.8.8.8/' "$install/.env"
expect "a public PROXY_IP" 1 'PROXY_IP must be one private'
sed -i 's/^PROXY_IP=.*/PROXY_IP=172.29.90.10/' "$install/.env"
echo 'OAUTH_GOOGLE_CLIENT_SECRET=x' >> "$install/.env"
expect "a client secret in .env" 1 'CLIENT_SECRET'
sed -i '/CLIENT_SECRET/d' "$install/.env"

# 2. The secret files (needs group 10010).
if (( can_gid )); then
  expect "good install" 0 '^install check'
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

(( fails == 0 )) || { echo "test-install-check: $fails failure(s)" >&2; exit 1; }
echo "test-install-check: ok"
