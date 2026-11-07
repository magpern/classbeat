# shellcheck shell=bash
# The owner's real PROXY_IP, the swag address and the no-client-secret rule for env files.
# Sourced by check-prod-compose.sh, never run on its own; it shares that script's shell, variables and functions. Part of the check-prod-compose split (#763): no check lives here that is not in the original.
# --- The owner's real PROXY_IP (#495) -------------------------------------------------------------
# Native path for docker on Git Bash (pwd -W): the file is under the checkout or $tmp.
native_path() { local p="$1"; [[ "$p" == "$root"/* ]] && p="$native/${p#"$root"/}"; printf '%s' "$p"; }
# Renders compose.prod.yaml with the owner's PROXY_IP: the shell's when set (compose prefers it),
# else the env file's (compose's own .env parsing: quotes, inline comments). The other variables
# stay the dummies above. Returns 2 when neither sets PROXY_IP.
check_owner_proxy_ip() {
  local file="$1" shell_set="$2" shell_val="$3" out value
  if [[ -n "$shell_set" ]]; then
    out="$(PROXY_IP="$shell_val" docker compose -f "$dir/compose.prod.yaml" config --format json 2>/dev/null)" \
      || fail "compose.prod.yaml does not render with PROXY_IP from the environment" || return 1
  elif [[ -f "$file" ]] && grep -Eq '^[[:space:]]*(export[[:space:]]+)?PROXY_IP[[:space:]]*=' "$file"; then
    out="$(env -u PROXY_IP docker compose --env-file "$(native_path "$file")" -f "$dir/compose.prod.yaml" \
      config --format json 2>/dev/null)" \
      || fail "compose.prod.yaml does not render with PROXY_IP from $file (empty?)" || return 1
  else
    return 2
  fi
  value="$(jq -r '.services.web.environment.CLASSBEAT_TRUSTED_PROXIES // ""' <<<"$out")"
  owner_trusted="$value"
  proxy_ip_ok "$value" || fail "the real PROXY_IP must be one private IPv4 address (swag's), not a range, list or name" \
    || return 1
}
rc=0; check_owner_proxy_ip "$env_file" "$owner_proxy_ip_set" "$owner_proxy_ip" || rc=$?
case "$rc" in
  0) owner_note="the real PROXY_IP is one private address" ;;
  2) owner_note="no real PROXY_IP set here (only dummies checked)" ;;
  *) exit 1 ;;
esac

# The real PROXY_IP against the address swag really has (#500): a typo such as .130 for .10 is inside
# Docker's allocation range, so the form check above passes it while web would trust nobody.
# swag_address_verdict PROXY_IP "ADDRESS..." (swag's addresses on its networks, empty when the
# container is not visible): 0 the address is one of them, 1 it is not, 2 nothing to compare.
swag_address_verdict() {
  local want="$1" have="$2" a
  [[ -n "${have//[[:space:]]/}" ]] || return 2
  for a in $have; do [[ "$a" == "$want" ]] && return 0; done
  fail "PROXY_IP is $want but the running swag container has ${have% }: set PROXY_IP to swag's address (runbook section 6a)" || return 1
}
# The running swag container's addresses (no output when it is not running or Docker cannot be asked).
swag_addresses() {
  local name="${PROD_CHECK_SWAG_CONTAINER:-swag}"
  [[ "$name" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]] || { fail "PROD_CHECK_SWAG_CONTAINER is not a container name"; return 1; }
  docker inspect --format '{{if .State.Running}}{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}{{end}}' \
    "$name" 2>/dev/null || true
}

# Where the real stack publishes web (install mode): only web publishes a port, and only on loopback
# (the host's own proxy) or on one private LAN address (compose.prod.lan.yaml, swag on another host),
# never on every interface; swag is not this server (#567).
check_real_ports() {   # check_real_ports RENDERED_JSON
  local j="$1" host trusted
  jq -e '[.services | to_entries[] | select(.key != "web") | select(.value.ports // [] | length > 0)] | length == 0' <<<"$j" >/dev/null \
    || fail "only web may publish a port" || return 1
  # As tight as the shipped overlays (check_loopback, check_lan): at most one port, tcp, to web's 8000.
  # Both overlays together, or a second port from an overlay of the owner's own, would publish web twice
  # (#470, review of #576). The host port may differ from 8080 (WEB_HOST_PORT).
  jq -e '(.services.web.ports // []) | length <= 1 and all(.target == 8000 and (.protocol // "tcp") == "tcp")' <<<"$j" >/dev/null \
    || fail "web publishes at most one port (container port 8000, tcp): use one overlay, not both (runbook section 6 or 6c)" || return 1
  trusted="$(jq -r '.services.web.environment.CLASSBEAT_TRUSTED_PROXIES // ""' <<<"$j")"
  while IFS= read -r host; do
    [[ "$host" == 127.0.0.1 ]] || proxy_ip_ok "$host" \
      || fail "web is published on '${host:-every interface}': only 127.0.0.1 or one private LAN address (WEB_LAN_ADDRESS), never 0.0.0.0, a name or IPv6" \
      || return 1
    [[ "$host" != "$trusted" ]] \
      || fail "PROXY_IP ($trusted) is this server's own published address: it must be swag's address on the other host" || return 1
  done < <(jq -r '.services.web.ports // [] | .[] | .host_ip // ""' <<<"$j")
}
if [[ "$mode" == install ]]; then
  check_real_ports "$real_json" || exit 1
  ( J="$real_all_json"; check_real ) \
    || { echo "check-prod-compose: the real render (your .env, COMPOSE_FILE and every profile) is refused (above)" >&2; exit 1; }
  # Only where web trusts PROXY_IP itself: not under the loopback overlay (the web-out gateway) and
  # not with a published port (the LAN overlay: swag is on another host).
  if [[ -n "${owner_trusted:-}" ]]; then
    if [[ "$(jq -r '.services.web.environment.CLASSBEAT_TRUSTED_PROXIES // ""' <<<"$real_json")" == "$owner_trusted" ]] \
        && jq -e '.services.web.ports // [] | length == 0' <<<"$real_json" >/dev/null; then
      rc=0; swag_address_verdict "$owner_trusted" "$(swag_addresses)" || rc=$?
      case "$rc" in
        0) owner_note="$owner_note; PROXY_IP is the running swag container's address" ;;
        2) owner_note="$owner_note; the swag container is not visible here: compare PROXY_IP with \`docker inspect\` by hand (runbook section 9)" ;;
        *) exit 1 ;;
      esac
    else
      owner_note="$owner_note; swag is not on the shared network of this host: PROXY_IP not compared with its address"
    fi
  fi
fi

# --- No OAuth client secret in an env file (#507) --------------------------------------------------
# A client secret goes in secrets/oauth_<provider>_client_secret, never in .env (compose would not
# pass it on anyway). Only the variable's name is matched; its value is never printed.
check_env_no_client_secret() {
  [[ -f "$1" ]] || return 0
  if grep -Eiq '^[[:space:]]*(export[[:space:]]+)?[a-z0-9_]*client_secret[a-z0-9_]*[[:space:]]*=' "$1"; then
    fail "$1 holds a *CLIENT_SECRET* variable: put a client secret in secrets/oauth_<provider>_client_secret"
    return 1
  fi
}
check_env_no_client_secret "$env_file" || exit 1
if [[ $mode == repo ]]; then   # the self-tests and the checkout-only checks (ADR 0020)
check_env_no_client_secret .env.example || exit 1
for line in 'OAUTH_GOOGLE_CLIENT_SECRET=x' 'export OAUTH_GITHUB_CLIENT_SECRET=' '  oauth_microsoft_client_secret = x' \
  'CLASSBEAT_OAUTH_GOOGLE_CLIENT_SECRET=x' 'CLIENT_SECRET=x'; do
  printf 'OAUTH_GOOGLE_CLIENT_ID=check\n%s\n' "$line" > "$tmp/oauth.env"
  if (check_env_no_client_secret "$tmp/oauth.env" 2>/dev/null); then
    echo "check-prod-compose: self-test: accepted an env file with '$line'" >&2; exit 1
  fi
done
printf 'OAUTH_GOOGLE_CLIENT_ID=123-abc.apps.googleusercontent.com\n# the secret is in secrets/oauth_google_client_secret\n' \
  > "$tmp/oauth.env"
check_env_no_client_secret "$tmp/oauth.env" || { echo "check-prod-compose: self-test: refused a good env file" >&2; exit 1; }
# Self-test: the env file's and the shell's value, as compose reads them.
owner_env="$tmp/owner.env"
expect_owner() {
  local want="$1" line="$2" shell_set="${3:-}" shell_val="${4:-}" rc=0
  printf '%s\n' "$line" > "$owner_env"
  (check_owner_proxy_ip "$owner_env" "$shell_set" "$shell_val" 2>/dev/null) || rc=$?
  [ "$rc" = "$want" ] || {
    echo "check-prod-compose: self-test: real PROXY_IP '$line' (shell '${shell_set:+$shell_val}'): got $rc, want $want" >&2
    exit 1
  }
}
for line in 'PROXY_IP=172.29.90.0/24   # the whole network' 'PROXY_IP=172.16.0.0/12' 'PROXY_IP=0.0.0.0/0' \
  'PROXY_IP=172.29.90.10,172.29.90.130' 'PROXY_IP=8.8.8.8' 'PROXY_IP=swag' 'PROXY_IP=fd00::1' 'PROXY_IP=' \
  'export PROXY_IP=172.29.90.10/32'; do
  expect_owner 1 "$line"
done
expect_owner 0 'PROXY_IP=172.29.90.10                            # swag (section 6)'
expect_owner 0 'PROXY_IP=" 172.29.90.10 "'
expect_owner 0 'PROXY_IP=172.29.80.1'
expect_owner 2 'CLASSBEAT_DOMAIN=classbeat.example.test'
expect_owner 1 'PROXY_IP=172.29.90.10' set 172.29.90.0/24   # the shell's value wins, as in compose
expect_owner 0 'PROXY_IP=172.29.90.0/24' set 172.29.90.10

fi
