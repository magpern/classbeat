#!/usr/bin/env bash
# Checks the production compose files and the sample swag site config with dummy values (#341, #492).
# No server, no secret, nothing contacted but the local Docker daemon (and its registry for the
# nginx image).
#   scripts/check-prod-compose.sh        (needs docker, jq; run from anywhere)
# 1. `docker compose config` accepts compose.prod.yaml alone (swag on a shared Docker network) and
#    with compose.prod.loopback.yaml (a proxy on the host, web on 127.0.0.1);
# 2. the invariants of the production stack hold for both;
# 3. a self-test: each unsafe mutation of the rendered files must fail step 2 (fails closed);
# 4. the owner's real PROXY_IP, when one is set (their shell, else PROD_CHECK_ENV_FILE, default the
#    checkout's .env): compose.prod.yaml rendered with it must trust one private IPv4 address (#495);
# 5. the runbook's `docker network create` keeps swag's fixed address outside Docker's --ip-range;
# 6. `nginx -t` accepts docs/runbooks/swag-classbeat.subdomain.conf, and the lines the app's
#    client address, body cap, SSE stream and headers rely on are in it (X-Forwarded-For set
#    exactly once);
# and a self-test for 4, 5 and 6 as for 2. On the server: run it in /opt/classbeat after writing
# .env (runbook section 9).
set -euo pipefail
export MSYS_NO_PATHCONV=1   # Git Bash: paths below are for the container, never converted

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"
# Git Bash on Windows: the native docker needs a Windows path (pwd -W); elsewhere it is plain pwd.
native="$(pwd -W 2>/dev/null || pwd)"
tmp="$(mktemp -d "$root/.prod-check.XXXXXX")"
trap 'rm -rf "$tmp"' EXIT
nginx_image="${PROD_CHECK_NGINX_IMAGE:-nginx:1.28.0}"
site=docs/runbooks/swag-classbeat.subdomain.conf
runbook=docs/runbooks/vps-deploy.md
# The owner's PROXY_IP, captured before the dummies below replace it (compose prefers the shell's).
owner_proxy_ip_set="${PROXY_IP+set}"
owner_proxy_ip="${PROXY_IP-}"
env_file="${PROD_CHECK_ENV_FILE:-$root/.env}"

# Copies next to dummy secret files, so the checkout's own secrets/ is never read.
cp compose.prod.yaml compose.prod.loopback.yaml "$tmp/"
mkdir "$tmp/secrets"
for n in postgres_password database_owner_password database_password database_maint_password \
  app_secret_key connection_kek smtp_password anthropic_api_key openai_api_key deepseek_api_key swedebeat_app; do
  : > "$tmp/secrets/$n"
done

swag_ip=172.29.90.10
export CLASSBEAT_VERSION=0.0.0 CLASSBEAT_DOMAIN=classbeat.example.test PROXY_IP="$swag_ip"
export SMTP_HOST=smtp.example.test SMTP_SENDER=noreply@example.test
export SMTP_CA_FILE=/run/smtp-ca/bridge.pem
export COMPOSE_PROJECT_NAME=classbeat-prod-check
unset COMPOSE_FILE PROXY_NETWORK WEB_HOST_PORT
dir="$native/${tmp##*/}"
shared="$(docker compose -f "$dir/compose.prod.yaml" config --format json)"
loopback="$(docker compose -f "$dir/compose.prod.yaml" -f "$dir/compose.prod.loopback.yaml" config --format json)"

J=""
fail() { echo "check-prod-compose: $*" >&2; return 1; }
q() { jq -e "$1" <<<"$J" >/dev/null; }

# One private IPv4 address (10/8, 172.16/12, 192.168/16), surrounding blanks aside as the app strips
# them: never a range, a list, a name, an IPv6 or a public address (#495).
proxy_ip_ok() {
  local v="$1" o
  v="${v#"${v%%[![:space:]]*}"}"; v="${v%"${v##*[![:space:]]}"}"
  [[ "$v" =~ ^(0|[1-9][0-9]{0,2})\.(0|[1-9][0-9]{0,2})\.(0|[1-9][0-9]{0,2})\.(0|[1-9][0-9]{0,2})$ ]] || return 1
  for o in "${BASH_REMATCH[@]:1}"; do (( o <= 255 )) || return 1; done
  local a="${BASH_REMATCH[1]}" b="${BASH_REMATCH[2]}"
  (( a == 10 || (a == 172 && b >= 16 && b <= 31) || (a == 192 && b == 168) ))
}

# Both variants. Each check returns 1 with its reason on the first violation.
check_common() {
  q '.services | has("caddy") | not' || fail "no caddy: swag terminates TLS (#492)" || return 1
  q '(.volumes // {} | keys) == ["db-data"]' || fail "the only named volume is db-data" || return 1
  q '.services | has("mailpit") | not' || fail "no mailpit in production" || return 1
  q '[.services | to_entries[] | select(.value.ports // [] | length > 0) | .key] | . == [] or . == ["web"]' \
    || fail "only web may publish a port (and only with the loopback overlay)" || return 1
  q '.services.db | (.ports // []) | length == 0' || fail "db must not publish ports" || return 1
  q '.networks.backend.internal == true' || fail "the backend network must be internal" || return 1
  q '[.services.db, .services.migrate, .services["db-functions"] | .networks | keys] | all(. == ["backend"])' \
    || fail "db, migrate and db-functions live on backend only" || return 1
  q '(.services.worker.networks | keys) == ["backend","egress"]' \
    || fail "worker joins backend and egress only" || return 1
  q '.services.web.read_only == true' || fail "web must be read-only" || return 1
  q '[.services.web, .services.worker] | all((.cap_drop // []) == ["ALL"] and ((.security_opt // []) | index("no-new-privileges:true")))' \
    || fail "web and worker drop every capability and set no-new-privileges" || return 1
  q '[.services.web, .services.db] | all(.healthcheck.test != null)' \
    || fail "web and db need their healthchecks" || return 1
  q '[.services.web.secrets[].source] | (index("anthropic_api_key") // -1) < 0' \
    || fail "web must not mount a model key" || return 1
  q '[.services | to_entries[] | select(.value.secrets // [] | map(.source) | index("swedebeat_app")) | .key] == ["web"]' \
    || fail "swedebeat_app belongs to web only" || return 1
  q '.services.web.environment.CLASSBEAT_SWEDEBEAT_ENABLED == "false"' \
    || fail "Swedebeat must be off by default" || return 1
  q '.services.worker.environment.CLASSBEAT_SWEDEBEAT_ENABLED == "false"' \
    || fail "Swedebeat must be off by default on the worker too" || return 1
  q '.services.web.environment.CLASSBEAT_RUNNING_ENABLED == "false"' \
    || fail "running classes must be off by default" || return 1
  q '.services.worker.environment.CLASSBEAT_RUNNING_ENABLED == "false"' \
    || fail "running classes must be off by default on the worker too" || return 1
  q '[.services.worker.secrets[].source] | index("database_maint_password") == null' \
    || fail "worker must not mount the maint password" || return 1
  q '[.services | to_entries[] | select(.value.secrets // [] | map(.source) | index("postgres_password")) | .key] | sort == ["db","db-functions"]' \
    || fail "postgres_password belongs to db and db-functions only" || return 1
  q '[.services | to_entries[] | select(.value.environment // {} | has("CLASSBEAT_SMTP_CA_FILE")) | .key] == ["web"]' \
    || fail "CLASSBEAT_SMTP_CA_FILE belongs to web only (migrate would fail on an unmounted file)" || return 1
  q '.services.web.environment.CLASSBEAT_SMTP_CA_FILE == "/run/smtp-ca/bridge.pem"' \
    || fail "web must get SMTP_CA_FILE" || return 1
  q '.services.web.environment.CLASSBEAT_ENVIRONMENT == "prod"' || fail "web must run as prod" || return 1
  q '.services.web.environment.CLASSBEAT_PUBLIC_BASE_URL == "https://classbeat.example.test"' \
    || fail "the public origin is https:// plus CLASSBEAT_DOMAIN" || return 1
  # One private IPv4 address, never a range or a list: the one peer whose X-Forwarded-For counts.
  proxy_ip_ok "$(jq -r '.services.web.environment.CLASSBEAT_TRUSTED_PROXIES // "" | tostring' <<<"$J")" \
    || fail "CLASSBEAT_TRUSTED_PROXIES must be one private IPv4 address" || return 1
  q '.networks["web-out"].ipam.config == [{"subnet":"172.29.80.0/24","gateway":"172.29.80.1"}] and (.networks["web-out"].external // false | not)' \
    || fail "web-out keeps its fixed subnet and gateway" || return 1
  q '[.services | to_entries[] | select(.value.networks // {} | has("web-out")) | .key] == ["web"]' \
    || fail "only web joins web-out" || return 1
  q '[.services[] | select((.logging.options["max-size"] // null) == null)] | length == 0' \
    || fail "every service needs log rotation" || return 1
}

# compose.prod.yaml alone: swag reaches web over the owner's external proxy network.
check_shared() {
  check_common || return 1
  q '[.services[] | select(.ports // [] | length > 0)] | length == 0' \
    || fail "no service publishes a port (swag owns 80 and 443)" || return 1
  q '.networks.proxy.external == true' || fail "the proxy network is the owner's external one" || return 1
  q '[.services | to_entries[] | select(.value.networks // {} | has("proxy")) | .key] == ["web"]' \
    || fail "only web joins the proxy network" || return 1
  q '.services.web.networks.proxy.aliases == ["classbeat-web"]' \
    || fail "web is classbeat-web on the proxy network" || return 1
  jq -e --arg ip "$swag_ip" '.services.web.environment.CLASSBEAT_TRUSTED_PROXIES == $ip' <<<"$J" >/dev/null \
    || fail "the trusted proxy is PROXY_IP (swag's fixed address)" || return 1
}

# With compose.prod.loopback.yaml: a proxy on the host reaches web on 127.0.0.1 only.
check_loopback() {
  check_common || return 1
  q '.services.web.ports | length > 0 and all(.host_ip == "127.0.0.1" and .target == 8000)' \
    || fail "web is published on 127.0.0.1 only" || return 1
  q '.services.web.networks | has("proxy") | not' || fail "web leaves the proxy network" || return 1
  q '.services.web.environment.CLASSBEAT_TRUSTED_PROXIES == .networks["web-out"].ipam.config[0].gateway' \
    || fail "the trusted proxy is the web-out gateway (Docker's port relay)" || return 1
}

J="$shared"; check_shared || exit 1
J="$loopback"; check_loopback || exit 1

# Self-test: every mutation is unsafe and must be refused.
expect_refused() {
  local variant="$1" base="$2" mutation="$3"
  J="$(jq "$mutation" <<<"$base")"
  if ("check_$variant" 2>/dev/null); then
    echo "check-prod-compose: self-test: accepted an unsafe $variant file: $mutation" >&2
    exit 1
  fi
}
for m in \
  '.services.caddy = {"image":"caddy:2.10.2","ports":[{"target":443,"published":"443"}],"logging":.services.web.logging}' \
  '.services.web.ports = [{"host_ip":"0.0.0.0","target":8000,"published":"8000"}]' \
  '.services.web.ports = [{"host_ip":"127.0.0.1","target":8000,"published":"8000"}]' \
  '.services.db.ports = [{"host_ip":"127.0.0.1","target":5432,"published":"5432"}]' \
  '.services.web.environment.CLASSBEAT_TRUSTED_PROXIES = "172.29.90.0/24"' \
  '.services.web.environment.CLASSBEAT_TRUSTED_PROXIES = "0.0.0.0/0"' \
  '.services.web.environment.CLASSBEAT_TRUSTED_PROXIES = "173.245.48.5"' \
  '.services.web.environment.CLASSBEAT_TRUSTED_PROXIES = "172.29.90.10,172.29.90.130"' \
  '.services.web.environment.CLASSBEAT_TRUSTED_PROXIES = "172.29.90.130"' \
  '.services.web.environment.CLASSBEAT_TRUSTED_PROXIES = ""' \
  '.services.worker.networks.proxy = null' \
  '.services.db.networks["web-out"] = null' \
  '.networks.proxy.external = false' \
  '.networks.backend.internal = false' \
  '.services.web.read_only = false' \
  '.services.web.cap_drop = []' \
  'del(.services.web.healthcheck)' \
  '.services.web.secrets += [{"source":"anthropic_api_key","target":"anthropic_api_key"}]' \
  '.services.web.environment.CLASSBEAT_PUBLIC_BASE_URL = "http://classbeat.example.test"' \
  '.services.web.environment.CLASSBEAT_ENVIRONMENT = "dev"' \
  '.services.web.environment.CLASSBEAT_RUNNING_ENABLED = "true"' \
  '.services.worker.environment.CLASSBEAT_SWEDEBEAT_ENABLED = "true"' \
  '.services.mailpit = {"image":"axllent/mailpit","logging":.services.web.logging}' \
  '.volumes["caddy-data"] = {}' \
  '.services.extra = {"image":"busybox","networks":{"proxy":null},"logging":.services.web.logging}' \
  '.services.extra = {"image":"busybox","networks":{"web-out":null},"logging":.services.web.logging}'; do
  expect_refused shared "$shared" "$m"
done
# The owner's PROXY_IP itself (it reaches CLASSBEAT_TRUSTED_PROXIES unchanged): a range, a list, a
# public or an empty address must be refused even when the variable and the setting agree.
good_ip="$swag_ip"
for bad in 172.29.90.0/24 172.29.90.10/32 0.0.0.0/0 8.8.8.8 173.245.48.5 "172.29.90.10,10.0.0.1" \
  172.32.0.1 172.29.90.256 172.29.090.10 fd00::1 swag ""; do
  swag_ip="$bad"
  expect_refused shared "$shared" ".services.web.environment.CLASSBEAT_TRUSTED_PROXIES = \"$bad\""
done
swag_ip="$good_ip"
for m in \
  '.services.web.ports[0].host_ip = "0.0.0.0"' \
  'del(.services.web.ports[0].host_ip)' \
  '.services.web.environment.CLASSBEAT_TRUSTED_PROXIES = "172.29.90.10"' \
  '.services.web.environment.CLASSBEAT_TRUSTED_PROXIES = "172.29.80.0/24"' \
  '.services.web.networks.proxy = {"aliases":["classbeat-web"]}' \
  '.services.worker.ports = [{"host_ip":"127.0.0.1","target":8000,"published":"8081"}]'; do
  expect_refused loopback "$loopback" "$m"
done

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
  proxy_ip_ok "$value" || fail "the real PROXY_IP must be one private IPv4 address (swag's), not a range, list or name" \
    || return 1
}
rc=0; check_owner_proxy_ip "$env_file" "$owner_proxy_ip_set" "$owner_proxy_ip" || rc=$?
case "$rc" in
  0) owner_note="the real PROXY_IP is one private address" ;;
  2) owner_note="no real PROXY_IP set here (only dummies checked)" ;;
  *) exit 1 ;;
esac
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

# --- The runbook's proxy network (#495) -----------------------------------------------------------
ip2int() { local a b c d; IFS=. read -r a b c d <<<"$1"; echo $(( (a << 24) | (b << 16) | (c << 8) | d )); }
in_cidr() {   # in_cidr ADDRESS NET/LEN
  local len="${2#*/}" mask
  mask=$(( len == 0 ? 0 : (0xFFFFFFFF << (32 - len)) & 0xFFFFFFFF ))
  (( ($(ip2int "$1") & mask) == ($(ip2int "${2%/*}") & mask) ))
}
cidr_re='[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+/[0-9]+'
ip_re='[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+'
# swag's fixed address is in the network's subnet but outside --ip-range, the pool Docker hands out
# to other containers, so none can take it while swag is down; it equals PROXY_IP's example and this
# script's dummy.
check_runbook() {
  local f="$1" create subnet range gw addr env
  create="$(grep -E '^docker network create ' "$f")" || fail "$f: no docker network create" || return 1
  [ "$(wc -l <<<"$create")" = 1 ] || fail "$f: one docker network create" || return 1
  grep -Eq -- '--internal( |$)' <<<"$create" || fail "$f: the proxy network is --internal" || return 1
  subnet="$(grep -Eo -- "--subnet $cidr_re" <<<"$create" | cut -d' ' -f2)" || fail "$f: --subnet" || return 1
  range="$(grep -Eo -- "--ip-range $cidr_re" <<<"$create" | cut -d' ' -f2)" \
    || fail "$f: --ip-range keeps swag's address out of Docker's pool" || return 1
  gw="$(grep -Eo -- "--gateway $ip_re" <<<"$create" | cut -d' ' -f2)" || fail "$f: --gateway" || return 1
  addr="$(grep -Eo "ipv4_address: $ip_re" "$f" | cut -d' ' -f2)" || fail "$f: swag's ipv4_address" || return 1
  env="$(grep -Eo "^PROXY_IP=$ip_re" "$f" | cut -d= -f2)" || fail "$f: the PROXY_IP example" || return 1
  [ "$(wc -l <<<"$addr")" = 1 ] && [ "$addr" = "$env" ] && [ "$addr" = "$good_ip" ] \
    || fail "$f: swag's ipv4_address, PROXY_IP's example and this script's dummy agree" || return 1
  (( ${range#*/} >= ${subnet#*/} )) && in_cidr "${range%/*}" "$subnet" \
    || fail "$f: --ip-range lies inside --subnet" || return 1
  in_cidr "$addr" "$subnet" && ! in_cidr "$addr" "$range" \
    || fail "$f: swag's address is in the subnet and outside --ip-range" || return 1
  in_cidr "$gw" "$subnet" && ! in_cidr "$gw" "$range" && [ "$gw" != "$addr" ] \
    || fail "$f: the gateway is in the subnet, outside --ip-range and not swag's" || return 1
}
check_runbook "$runbook" || exit 1
expect_runbook_refused() {
  sed -E "$1" "$runbook" > "$tmp/runbook.md"
  if (check_runbook "$tmp/runbook.md" 2>/dev/null); then
    echo "check-prod-compose: self-test: accepted an unsafe runbook: $1" >&2; exit 1
  fi
}
for m in \
  's/ --ip-range [^ ]+//' \
  's|--ip-range [^ ]+|--ip-range 172.29.90.0/25|' \
  's|--ip-range [^ ]+|--ip-range 172.29.90.8/29|' \
  's|--ip-range [^ ]+|--ip-range 172.29.0.0/16|' \
  's/ipv4_address: [0-9.]+/ipv4_address: 172.29.90.200/' \
  's/^PROXY_IP=[0-9.]+/PROXY_IP=172.29.90.11/' \
  's|--gateway [^ ]+|--gateway 172.29.90.10|' \
  's/ --internal//'; do
  expect_runbook_refused "$m"
done

# --- The swag site config: the lines the app relies on, then nginx's own syntax check -------------
sfail() { echo "check-prod-compose: $site: $*" >&2; return 1; }
# shellcheck disable=SC2016  # literal nginx variables, not shell ones
check_site() {
  local code n
  code="$(sed -e 's/#.*//' "$1")"   # comments do not count
  has() { grep -Eq -- "$1" <<<"$code"; }
  # X-Forwarded-For exactly once, overwritten with $remote_addr: a second line (whatever its value)
  # sends a visitor-chosen entry right of swag's, and the app takes the right-most (#495).
  n="$(grep -Eic -- "^[[:space:]]*proxy_set_header[[:space:]]+[\"']?x-forwarded-for[\"']?([[:space:]]|$)" <<<"$code" || true)"
  [ "$n" = 1 ] || sfail "exactly one proxy_set_header X-Forwarded-For (found $n)" || return 1
  has '^[[:space:]]*proxy_set_header X-Forwarded-For \$remote_addr;[[:space:]]*$' \
    || sfail "X-Forwarded-For must be overwritten with \$remote_addr" || return 1
  if has 'proxy_add_x_forwarded_for|include /config/nginx/proxy\.conf'; then
    sfail "never append to a visitor's X-Forwarded-For (no proxy.conf)"; return 1
  fi
  # Only swag's TLS settings and the Cloudflare list are included: anything else could set headers.
  if grep -E '^[[:space:]]*include[[:space:]]' <<<"$code" \
      | grep -Evq '^[[:space:]]*include /config/nginx/(ssl|cf_real-ip)\.conf;[[:space:]]*$'; then
    sfail "include only /config/nginx/ssl.conf and cf_real-ip.conf"; return 1
  fi
  has '^[[:space:]]*client_max_body_size 1m;' || sfail "client_max_body_size 1m (the app's cap is 64 KiB)" || return 1
  if has 'add_header[[:space:]]+(Content-Security-Policy|Referrer-Policy)|proxy_hide_header|more_clear_headers'; then
    sfail "the app's CSP and Referrer-Policy are neither added, hidden nor replaced here"; return 1
  fi
  if ! has '^[[:space:]]*server_name [a-z0-9.-]+;' || has 'server_name[^;]*\*'; then
    sfail "server_name is the exact host"; return 1
  fi
  has '^[[:space:]]*server_tokens off;' || sfail "server_tokens off (no nginx version)" || return 1
  if has 'server_tokens[[:space:]]+(on|build)|proxy_intercept_errors[[:space:]]+on'; then
    sfail "no server_tokens on; the app's own errors pass through (no proxy_intercept_errors)"; return 1
  fi
  has '^[[:space:]]*real_ip_header CF-Connecting-IP;' || sfail "real IP from CF-Connecting-IP (Cloudflare ranges only)" || return 1
  if has 'real_ip_recursive[[:space:]]+on|set_real_ip_from'; then
    sfail "Cloudflare ranges belong in cf_real-ip.conf only, non-recursive"; return 1
  fi
  has 'add_header Strict-Transport-Security' || sfail "HSTS is the edge's job" || return 1
  # The SSE location: unbuffered, long read timeout, and no proxy_set_header or add_header in any
  # location (one there would silently drop the server-level forwarded headers or edge headers).
  awk '
    /^[[:space:]]*location / { inloc = 1; sse = ($0 ~ /events/); next }
    inloc && /^[[:space:]]*}/ { inloc = 0; next }
    inloc && /(proxy_set_header|add_header)/ { print "location sets " $1; bad = 1 }
    inloc && sse && /proxy_buffering off;/ { buf = 1 }
    inloc && sse && /proxy_read_timeout (9[0-9][0-9]|[1-9][0-9][0-9][0-9]+)s;/ { rt = 1 }
    END { if (!buf) print "the SSE location needs proxy_buffering off"; if (!rt) print "the SSE location needs proxy_read_timeout of at least 900s"; exit (bad || !buf || !rt) }
  ' <<<"$code" >&2 || sfail "location blocks" || return 1
}
check_site "$site" || exit 1
expect_site_refused() {
  sed -E "$1" "$site" > "$tmp/site-mutated.conf"
  if (check_site "$tmp/site-mutated.conf" 2>/dev/null); then
    echo "check-prod-compose: self-test: accepted an unsafe site config: $1" >&2; exit 1
  fi
}
# shellcheck disable=SC2016  # literal nginx variables, not shell ones
for m in \
  '/proxy_set_header X-Forwarded-For \$remote_addr;/a\    proxy_set_header X-Forwarded-For $http_x_forwarded_for;' \
  '/proxy_set_header X-Forwarded-For \$remote_addr;/a\    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;' \
  '/proxy_set_header X-Forwarded-For \$remote_addr;/a\    proxy_set_header x-forwarded-for "6.6.6.6";' \
  '/proxy_set_header X-Forwarded-For \$remote_addr;/i\    proxy_set_header "X-Forwarded-For" $http_x_forwarded_for;' \
  '/proxy_set_header X-Forwarded-For \$remote_addr;/d' \
  's/proxy_set_header X-Forwarded-For \$remote_addr;/proxy_set_header X-Forwarded-For $http_x_forwarded_for;/' \
  '/include \/config\/nginx\/ssl.conf;/a\    include /config/nginx/proxy.conf;' \
  '/include \/config\/nginx\/ssl.conf;/a\    include /config/nginx/extra-headers.conf;' \
  '/server_tokens off;/d' \
  's/server_tokens off;/server_tokens on;/' \
  '/error_page 502/a\    proxy_intercept_errors on;' \
  '/client_max_body_size 1m;/d' \
  '/add_header Strict-Transport-Security/a\    add_header Content-Security-Policy "default-src *" always;' \
  's/proxy_buffering off;/proxy_buffering on;/' \
  '/return 502/a\        add_header Cache-Control "no-store" always;' \
  's/real_ip_header CF-Connecting-IP;/real_ip_header X-Forwarded-For;/'; do
  expect_site_refused "$m"
done

mkdir -p "$tmp/nginx"
printf 'ssl_certificate /tmp/cert.pem;\nssl_certificate_key /tmp/key.pem;\n' > "$tmp/nginx/ssl.conf"
printf 'set_real_ip_from 173.245.48.0/20;\n' > "$tmp/nginx/cf_real-ip.conf"
printf 'events {}\nhttp {\n  resolver 127.0.0.11;\n  include /config/nginx/proxy-confs/classbeat.subdomain.conf;\n}\n' > "$tmp/nginx/nginx.conf"
cp "$site" "$tmp/nginx/site.conf"
nginx_t() {
  docker run --rm --network none \
    -v "$dir/nginx/site.conf:/config/nginx/proxy-confs/classbeat.subdomain.conf:ro" \
    -v "$dir/nginx/ssl.conf:/config/nginx/ssl.conf:ro" \
    -v "$dir/nginx/cf_real-ip.conf:/config/nginx/cf_real-ip.conf:ro" \
    -v "$dir/nginx/nginx.conf:/check/nginx.conf:ro" \
    "$nginx_image" sh -c 'set -e; mkdir -p /config/log/nginx
      openssl req -x509 -newkey rsa:2048 -nodes -subj /CN=classbeat.example.test -days 1 \
        -keyout /tmp/key.pem -out /tmp/cert.pem 2>/dev/null
      nginx -t -q -c /check/nginx.conf' >"$tmp/nginx.log" 2>&1
}
if ! nginx_t; then
  # nginx -t opens its listening sockets; a kernel without IPv6 (some VMs) cannot open [::]:443.
  # Only then, check the rest without that one line and say so.
  grep -q 'Address family not supported' "$tmp/nginx.log" || { cat "$tmp/nginx.log" >&2; sfail "nginx -t failed"; exit 1; }
  sed -i '/listen \[::\]:443 ssl;/d' "$tmp/nginx/site.conf"
  nginx_t || { cat "$tmp/nginx.log" >&2; sfail "nginx -t failed"; exit 1; }
  echo "check-prod-compose: no IPv6 here: nginx -t ran without the [::]:443 listen line" >&2
fi

echo "compose.prod.yaml (shared network and loopback), runbook network, swag site config and self-tests: ok; $owner_note"
