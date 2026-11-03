#!/usr/bin/env bash
# Checks the production compose files and the sample swag site config with dummy values (#341, #492).
# No server, no secret, nothing contacted but the local Docker daemon (and its registry for the
# nginx image).
#   scripts/check-prod-compose.sh        (needs docker, jq; run from anywhere)
# 1. `docker compose config` accepts compose.prod.yaml alone (swag on a shared Docker network) and
#    with compose.prod.loopback.yaml (a proxy on the host, web on 127.0.0.1);
# 2. the invariants of the production stack hold for both;
# 3. a self-test: each unsafe mutation of the rendered files must fail step 2 (fails closed);
# 4. `nginx -t` accepts docs/runbooks/swag-classbeat.subdomain.conf, and the lines the app's
#    client address, body cap, SSE stream and headers rely on are in it.
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
  q '.services.web.environment.CLASSBEAT_TRUSTED_PROXIES | test("^(10\\.[0-9]{1,3}|172\\.(1[6-9]|2[0-9]|3[01])|192\\.168)\\.[0-9]{1,3}\\.[0-9]{1,3}$")' \
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
  '.services.web.environment.CLASSBEAT_TRUSTED_PROXIES = "172.29.90.10,172.29.90.11"' \
  '.services.web.environment.CLASSBEAT_TRUSTED_PROXIES = "172.29.90.11"' \
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
for bad in 172.29.90.0/24 0.0.0.0/0 8.8.8.8 173.245.48.5 "172.29.90.10,10.0.0.1" 172.32.0.1 ""; do
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

# The swag site config: the lines the app relies on, then nginx's own syntax check.
sfail() { echo "check-prod-compose: $site: $*" >&2; exit 1; }
code="$(sed -e 's/#.*//' "$site")"   # comments do not count
has() { grep -Eq -- "$1" <<<"$code"; }
# shellcheck disable=SC2016  # a literal nginx variable, not a shell one
has '^[[:space:]]*proxy_set_header X-Forwarded-For \$remote_addr;' \
  || sfail "X-Forwarded-For must be overwritten with \$remote_addr"
has 'proxy_add_x_forwarded_for|include /config/nginx/proxy\.conf' \
  && sfail "never append to a visitor's X-Forwarded-For (no proxy.conf)"
has '^[[:space:]]*client_max_body_size 1m;' || sfail "client_max_body_size 1m (the app's cap is 64 KiB)"
has 'add_header[[:space:]]+(Content-Security-Policy|Referrer-Policy)|proxy_hide_header|more_clear_headers' \
  && sfail "the app's CSP and Referrer-Policy are neither added, hidden nor replaced here"
if ! has '^[[:space:]]*server_name [a-z0-9.-]+;' || has 'server_name[^;]*\*'; then
  sfail "server_name is the exact host"
fi
has '^[[:space:]]*real_ip_header CF-Connecting-IP;' || sfail "real IP from CF-Connecting-IP (Cloudflare ranges only)"
has 'real_ip_recursive[[:space:]]+on|set_real_ip_from' && sfail "Cloudflare ranges belong in cf_real-ip.conf only, non-recursive"
has 'add_header Strict-Transport-Security' || sfail "HSTS is the edge's job"
# The SSE location: unbuffered, long read timeout, and no proxy_set_header or add_header in any
# location (one there would silently drop the server-level forwarded headers or edge headers).
awk '
  /^[[:space:]]*location / { inloc = 1; sse = ($0 ~ /events/); next }
  inloc && /^[[:space:]]*}/ { inloc = 0; next }
  inloc && /(proxy_set_header|add_header)/ { print "location sets " $1; bad = 1 }
  inloc && sse && /proxy_buffering off;/ { buf = 1 }
  inloc && sse && /proxy_read_timeout (9[0-9][0-9]|[1-9][0-9][0-9][0-9]+)s;/ { rt = 1 }
  END { if (!buf) print "the SSE location needs proxy_buffering off"; if (!rt) print "the SSE location needs proxy_read_timeout of at least 900s"; exit (bad || !buf || !rt) }
' <<<"$code" >&2 || sfail "location blocks"

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
  grep -q 'Address family not supported' "$tmp/nginx.log" || { cat "$tmp/nginx.log" >&2; sfail "nginx -t failed"; }
  sed -i '/listen \[::\]:443 ssl;/d' "$tmp/nginx/site.conf"
  nginx_t || { cat "$tmp/nginx.log" >&2; sfail "nginx -t failed"; }
  echo "check-prod-compose: no IPv6 here: nginx -t ran without the [::]:443 listen line" >&2
fi

echo "compose.prod.yaml (shared network and loopback), self-test and swag site config: ok"
