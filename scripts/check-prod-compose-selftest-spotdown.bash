# shellcheck shell=bash
# Self-test of the link to spotdown (repo mode, #1526): every unsafe shape is refused, each for its own reason.
# Sourced by check-prod-compose.sh after check-prod-compose-selftest-compose.bash (expect_refused,
# expect_accepted); never run on its own; it shares that script's shell, variables and functions.
hosted_no_service='no service named spotdown'
hosted_no_image='no service runs a spotdown image'
hosted_no_volume='no volume named spotdown'
hosted_net='the only spotdown network is the external one'
plain_svc='{"image":"example.test/analysis:1.0.0","logging":.services.web.logging}'
# What classbeat never runs, on every production render (the check names each piece it refuses).
hosted=(
  "$hosted_no_service|.services[\"spotdown-api\"] = $plain_svc"
  "$hosted_no_service|.services[\"spotdown-worker\"] = $plain_svc"
  "$hosted_no_service|.services[\"spotdown-redis\"] = {\"image\":\"redis:7\",\"logging\":.services.web.logging}"
  "$hosted_no_service|.services.spotdown = $plain_svc"
  "$hosted_no_image|.services.analysis = {\"image\":\"ghcr.io/magpern/spotdown:1.2.3\",\"logging\":.services.web.logging}"
  "$hosted_no_image|.services.analysis = {\"image\":\"GHCR.io/magpern/Spotdown:latest\",\"logging\":.services.web.logging}"
  "$hosted_no_image|.services.analysis = {\"image\":\"example.test/spotdown:0.0.0\",\"logging\":.services.web.logging}"
  "$hosted_no_volume|.volumes[\"spotdown-data\"] = {\"name\":\"classbeat-prod-check_spotdown-data\"}"
  "$hosted_no_volume|.volumes.spotdown = {}"
  "$hosted_no_volume|.services.worker.volumes = [{\"type\":\"volume\",\"source\":\"spotdown-redis\",\"target\":\"/data\"}]"
  "$hosted_net|.networks.spotdown = {\"name\":\"classbeat-prod-check_spotdown\",\"internal\":true} | .services.worker.networks.spotdown = null"
  "$hosted_net|.networks.spotdown = {\"name\":\"spotdown\"} | .services.worker.networks.spotdown = null"
  "$hosted_net|.networks[\"spotdown-internal\"] = {\"name\":\"spotdown\",\"external\":true}"
  "$hosted_net|.networks[\"spotdown-egress\"] = {\"name\":\"classbeat-prod-check_spotdown-egress\",\"ipam\":{}}"
)
for b in shared loopback lan spotdown; do
  case "$b" in shared) v=shared base="$shared" ;; loopback) v=loopback base="$loopback" ;; lan) v=lan base="$lan" ;;
    *) v=withspotdown base="$spotdown" ;; esac
  for e in "${hosted[@]}"; do expect_refused "$v" "$base" "${e#*|}" "${e%%|*}"; done
done
for e in "${hosted[@]}"; do expect_refused real "$spotdown" "${e#*|}" "${e%%|*}"; done   # and the owner's real render
# The link, whole: the worker only gets the URL, the key and the external network.
link=(
  "only classbeat's worker joins the spotdown network|.services.web.networks.spotdown = null"
  "db, migrate and db-functions live on backend only|.services.migrate.networks.spotdown = null"
  "only classbeat's worker joins the spotdown network|.services.worker.networks |= del(.spotdown)"
  "worker joins backend and egress only|.services.worker.networks.proxy = null"
  "worker joins backend and egress only|.services.worker.networks[\"web-out\"] = null"
  "belongs to classbeat's worker only (never web)|.services.web.secrets += [{\"source\":\"spotdown_service_key\"}]"
  "belongs to classbeat's worker only (never web)|.services.migrate.secrets += [{\"source\":\"spotdown_service_key\"}]"
  "belongs to classbeat's worker only (never web)|.services.worker.secrets |= map(select(.source != \"spotdown_service_key\"))"
  "declares the secret spotdown_service_key|del(.secrets.spotdown_service_key)"
  "each secret is the file secrets/<its own name>|.secrets.spotdown_service_key.file = \"/etc/hostname\""
  "services.web.environment: 1 variable|.services.web.environment.CLASSBEAT_SPOTDOWN_URL = \"http://spotdown-api:8000\""
  "services.worker.environment: 1 variable|.services.worker.environment.CLASSBEAT_SPOTDOWN_KEY = \"x\""
  "CLASSBEAT_SPOTDOWN_URL is set|.services.worker.environment.CLASSBEAT_SPOTDOWN_URL = \"\""
  "CLASSBEAT_SPOTDOWN_URL is set|del(.services.worker.environment.CLASSBEAT_SPOTDOWN_URL)"
  "CLASSBEAT_SPOTDOWN_URL defaults to http://spotdown-api:8000|.services.worker.environment.CLASSBEAT_SPOTDOWN_URL = \"https://spotdown.example.test\""
  "networks.spotdown is the external network named spotdown|.networks.spotdown.name = \"bridge\""
  "networks.spotdown is the external network named spotdown|.networks.spotdown.internal = true"
  "networks.spotdown is the external network named spotdown|.networks.spotdown.ipam = {\"config\":[{\"subnet\":\"10.9.0.0/24\"}]}"
  "no network alias|.services.worker.networks.spotdown = {\"aliases\":[\"db\"]}"
  "services.worker.secrets[]|.services.worker.secrets |= map(.mode = 292)"
)
for e in "${link[@]}"; do expect_refused withspotdown "$spotdown" "${e#*|}" "${e%%|*}"; done
expect_accepted withspotdown "$spotdown" '.'
expect_accepted real "$spotdown" '.'
# The owner's own URL passes the render check (the worker's url_policy is the one rule on its value).
expect_accepted real "$spotdown" '.services.worker.environment.CLASSBEAT_SPOTDOWN_URL = "http://10.0.0.5:8000"'
expect_refused real "$spotdown" '.services.worker.environment.CLASSBEAT_SPOTDOWN_URL = ""' 'CLASSBEAT_SPOTDOWN_URL is set'
expect_refused real "$spotdown" '.services.web.networks.spotdown = null' "only classbeat's worker joins the spotdown network"
# Without the overlay, any one piece of the link is refused: all of it or none of it.
for v in shared loopback lan; do
  case "$v" in shared) base="$shared" ;; loopback) base="$loopback" ;; *) base="$lan" ;; esac
  expect_refused "$v" "$base" '.services.worker.environment.CLASSBEAT_SPOTDOWN_URL = "http://spotdown-api:8000"' 'networks.spotdown is the external network named spotdown'
  expect_refused "$v" "$base" '.secrets.spotdown_service_key = {"file": (.secrets.app_secret_key.file | rtrimstr("app_secret_key") + "spotdown_service_key")} | .services.worker.secrets += [{"source":"spotdown_service_key"}]' 'networks.spotdown is the external network named spotdown'
  expect_refused "$v" "$base" '.networks.spotdown = {"name":"spotdown","external":true} | .services.worker.networks.spotdown = null' 'declares the secret spotdown_service_key'
done
# The empty-key check (install mode, the overlay in COMPOSE_FILE): size only, nothing is printed.
sk_dir="$tmp/spotdown-key"; mkdir "$sk_dir"
sk_canary="spotdown-canary-$RANDOM$RANDOM"
sk_expect() {   # sk_expect WANT(ok|refused) SPOTDOWN(on|off) KEY(none|empty|full)
  local want="$1" j='{"secrets":{"app_secret_key":{}}}' rc=0 out
  [[ "$2" == off ]] || j='{"secrets":{"app_secret_key":{},"spotdown_service_key":{}}}'
  rm -f "$sk_dir/spotdown_service_key"
  case "$3" in empty) : > "$sk_dir/spotdown_service_key" ;; full) printf '%s' "$sk_canary" > "$sk_dir/spotdown_service_key" ;; esac
  out="$( (check_spotdown_key "$j" "$sk_dir") 2>&1)" || rc=$?
  if [[ "$want" == ok && "$rc" != 0 ]] || [[ "$want" == refused && "$rc" == 0 ]] || grep -qF -- "$sk_canary" <<<"$out"; then
    echo "check-prod-compose: self-test: spotdown $2, key $3: wanted $want, rc $rc (or the key was printed)" >&2; exit 1
  fi
}
sk_expect ok on full
sk_expect refused on empty
sk_expect refused on none
sk_expect ok off none
sk_expect ok off empty
