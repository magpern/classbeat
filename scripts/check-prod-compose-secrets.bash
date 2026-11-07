# shellcheck shell=bash
# The secret-mount and OAuth rules, the per-variant checks and their first run.
# Sourced by check-prod-compose.sh, never run on its own; it shares that script's shell, variables and functions. Part of the check-prod-compose split (#763): no check lives here that is not in the original.
# Secret files (#511). Outside Swarm a file secret is a bind mount with the host file's owner and
# mode, so every service that mounts one runs as a non-root user with the group of the files
# (secrets_gid). The postgres image's root entrypoint re-execs as postgres through gosu, which drops
# supplementary groups: a postgres service runs as postgres from the start. Each top-level secret is
# the file secrets/<its own name> (no second name for a file, #508 review), mounted at its own name,
# and nothing reaches secrets/ another way: no bind mount at all, and (check_keys, default deny) no
# configs, post_start/pre_stop hook, volumes_from, shared pid or ipc namespace, privileged,
# devices, added capability or named volume with driver options, because none of those keys is on the
# allow-list; no command, entrypoint or healthcheck names /run/secrets or secrets/ (a plain text match
# against honest mistakes; a reviewed compose file is the guard against intent).
secrets_gid=10010
# shellcheck disable=SC2016  # jq variables, not shell ones
check_secret_mounts() {
  jq -e --arg g "$secrets_gid" '[.services[] | select((.secrets // []) | length > 0)
      | (.group_add // []) | map(tostring) | index($g)] | all(. != null)' <<<"$J" >/dev/null \
    || fail "every service that mounts a secret has group_add [\"$secrets_gid\"]" || return 1
  # Numerically: "00", "+0", "0000:0" and "postgres:0" are uid or gid 0 too (#512 review). Every
  # service, not only those with a secret: none runs as root.
  q '[.services[] | .user // "" | tostring | split(":")[] | select(test("^[+-]?0+$") or . == "root")] | length == 0' \
    || fail "no service runs as root (uid or gid 0 or root, however written)" || return 1
  q '[.services[] | select((.secrets // []) | length > 0) | select(.image // "" | test("(^|/)(postgres|classbeat-db)([:@]|$)"))
      | .user // "" | tostring] | all(. == "postgres" or . == "999" or . == "999:999")' \
    || fail "a postgres service that mounts a secret runs as postgres (gosu would drop the group)" || return 1
  # Only the app image has a non-root USER of its own; any other image must name one.
  q '[.services[] | select((.secrets // []) | length > 0) | select(.image // "" | startswith("ghcr.io/magpern/classbeat:") | not)
      | .user // "" | tostring | select(. == "")] | length == 0' \
    || fail "a service that mounts a secret and is not the app image names its non-root user" || return 1
  # With one shared group, any secret file that reaches a container is readable there. volumes_from,
  # a shared pid or ipc namespace, configs, hooks, privileged, devices, added capabilities and a named
  # volume's driver options are refused by check_keys (none is on the allow-list); what follows checks
  # the values of the keys that are.
  q '(.secrets.app_secret_key.file // "" | rtrimstr("/app_secret_key")) as $sd | $sd != "" and $sd != (.secrets.app_secret_key.file // "")
     and (.secrets | to_entries | all(.value | type == "object" and ((keys - ["name","file"]) == [])) )
     and (.secrets | to_entries | all(.value.file == $sd + "/" + .key))' \
    || fail "each secret is the file secrets/<its own name> and nothing else" || return 1
  q '[.services[] | .secrets // [] | .[] | (.target // .source) as $t | $t == .source or $t == "/run/secrets/" + .source]
     | all' \
    || fail "each secret is mounted at its own name in /run/secrets" || return 1
  q '(.secrets.app_secret_key.file | rtrimstr("/app_secret_key")) as $sd
     | [.services[] | .volumes // [] | .[] | select(.type == "bind") | .source // "" | rtrimstr("/")
        | select(. as $s | $s == $sd or ($sd | startswith($s + "/")) or ($s | startswith($sd + "/")))] | length == 0' \
    || fail "no service bind-mounts secrets/, a folder above it or a file in it" || return 1
  # An allow-list, not a text match against secrets/ (#512 review B2): /proc, /proc/1/root, the
  # Docker socket, /sys, /dev or a link to secrets/ would all reach another container's secrets
  # through the shared group. Production mounts nothing but the named volume and tmpfs: the SQL the
  # database services run is in the classbeat-db image (ADR 0020), so no host path is bound at all.
  # (The owner's uncommitted compose.bridge.yaml, proton-bridge-smtp.md, is not rendered here.)
  q '[.services[] | .volumes // [] | .[] | select(.type != "volume" and .type != "tmpfs")] | length == 0' \
    || fail "no bind mounts: only the named volume and tmpfs (the SQL is in the classbeat-db image; no /proc, socket or other host path)" \
    || return 1
  q '[.services[] | [.command, .entrypoint, .healthcheck.test] | tostring | select(test("run/secrets|secrets/"))] | length == 0' \
    || fail "no command, entrypoint or healthcheck names /run/secrets or secrets/ (the app reads its files itself)" \
    || return 1
}

# OAuth sign-in (#507, ADR 0003 section 5). Each client secret is a file secret under secrets/,
# mounted on web only at /run/secrets/<name> (where the app reads it); no service has a variable
# whose name says client secret; the OAuth settings are web's only; the canary in the dummy secret
# files appears nowhere in the rendered files (no `content:` or `environment:` secret, no literal).
check_oauth() {
  local p n
  for p in "${oauth_providers[@]}"; do
    n="oauth_${p}_client_secret"
    jq -e --arg n "$n" '[.services | to_entries[] | select(.value.secrets // [] | map(.source) | index($n)) | .key] == ["web"]' \
      <<<"$J" >/dev/null || fail "$n is mounted on web only" || return 1
    jq -e --arg n "$n" '[.services.web.secrets[] | select(.source == $n)] | length == 1 and all((.target // $n) | . == $n or . == "/run/secrets/" + $n)' \
      <<<"$J" >/dev/null || fail "$n is mounted once, at /run/secrets/$n" || return 1
    jq -e --arg n "$n" '.secrets[$n] | type == "object" and ((keys - ["name","file"]) == []) and (.file // "" | endswith("/secrets/" + $n))' \
      <<<"$J" >/dev/null || fail "$n is the file secrets/$n and nothing else" || return 1
  done
  q '[.services[] | .environment // {} | keys[] | select(test("SECRET"; "i"))] | length == 0' \
    || fail "no service has a secret in a variable (OAuth client secrets are files)" || return 1
  q '[.services | to_entries[] | select(.value.environment // {} | keys | any(startswith("CLASSBEAT_OAUTH_"))) | .key] == ["web"]' \
    || fail "the OAuth settings belong to web only" || return 1
  q '(.services.web.environment | with_entries(select(.key | startswith("CLASSBEAT_OAUTH_"))) | keys)
     == ["CLASSBEAT_OAUTH_GITHUB_CLIENT_ID","CLASSBEAT_OAUTH_GOOGLE_CLIENT_ID","CLASSBEAT_OAUTH_MICROSOFT_CLIENT_ID","CLASSBEAT_OAUTH_VISIBLE_PROVIDERS"]' \
    || fail "web gets the three OAuth client ids and the visible providers, nothing else" || return 1
  q '.services.web.environment | .CLASSBEAT_OAUTH_GOOGLE_CLIENT_ID == "check-google-client-id"
     and .CLASSBEAT_OAUTH_GITHUB_CLIENT_ID == "check-github-client-id"
     and .CLASSBEAT_OAUTH_MICROSOFT_CLIENT_ID == "check-microsoft-client-id"
     and .CLASSBEAT_OAUTH_VISIBLE_PROVIDERS == "google,github"' \
    || fail "the OAuth client ids and visible providers come from their variables" || return 1
  if grep -Fq -- "$oauth_canary" <<<"$J"; then
    fail "an OAuth client secret's value is in the rendered compose file"; return 1
  fi
}

# compose.prod.yaml without any OAuth variable: every provider off, Google the only visible name.
check_oauth_defaults() {
  q '.services.web.environment | .CLASSBEAT_OAUTH_GOOGLE_CLIENT_ID == "" and .CLASSBEAT_OAUTH_GITHUB_CLIENT_ID == ""
     and .CLASSBEAT_OAUTH_MICROSOFT_CLIENT_ID == "" and .CLASSBEAT_OAUTH_VISIBLE_PROVIDERS == "google"' \
    || fail "without its variable, each OAuth client id is empty (provider off); visible providers default to google" \
    || return 1
  check_oauth_files "$tmp" || return 1
  check_prod_file_text "$tmp" || return 1
}

# A fast lint (#517) of keys production never uses: env_file (Compose copies the file into
# `environment`, where a file of secrets/ would sit in the render and `docker inspect`), label_file,
# `profiles` (hides a service from a render without `--profile '*'`), `extends` and `include`. It reads
# lines, so a quoted or explicit (`? env_file`) key can get past it: the check is the render's
# (check_keys: variables by name, label_file and profiles by key, #610). Comment lines are skipped; a
# mention after code on a line is not.
check_prod_file_text() {   # check_prod_file_text DIR [FILE...] (default: the three production files)
  local d="$1" f files=(compose.prod.yaml compose.prod.loopback.yaml compose.prod.lan.yaml)
  local re=$'(^|[[:space:]{,"\'])(env_file|label_file|profiles|extends|include)["\']?[[:space:]]*:'
  shift
  (( $# == 0 )) || files=("$@")
  for f in "${files[@]}"; do
    if grep -Ev '^[[:space:]]*#' "$d/$f" | grep -Eq -- "$re"; then
      fail "$f: no env_file, label_file, profiles, extends or include in the production files (the render drops them)"; return 1
    fi
  done
}

# The compose files as written: no variable named like a client secret, no client secret literal.
# check_oauth_files DIR [FILE...] (default: the three production files; the repo mode adds the dev
# compose.yaml, #521).
check_oauth_files() {
  local d="$1" f files=(compose.prod.yaml compose.prod.loopback.yaml compose.prod.lan.yaml)
  shift
  (( $# == 0 )) || files=("$@")
  for f in "${files[@]}"; do
    if grep -E -i 'client_secret' "$d/$f" | grep -Evq '^[[:space:]]*(#.*|- oauth_(google|github|microsoft)_client_secret|oauth_(google|github|microsoft)_client_secret:|file: \./secrets/oauth_(google|github|microsoft)_client_secret)[[:space:]]*$'; then
      fail "$f: client_secret only as the three secret names and their secrets/ files"; return 1
    fi
  done
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

# With compose.prod.lan.yaml: swag on another host reaches web on this server's LAN address (#567).
# Exactly one published port, on the one private address, not loopback, not a wildcard, not IPv6
# (an empty or missing host_ip is a wildcard), web off the proxy network, swag (PROXY_IP) the one
# trusted peer and not this server.
# shellcheck disable=SC2016  # jq variables, not shell ones
check_lan() {
  check_common || return 1
  q --arg ip "$lan_ip" '.services.web.ports | length == 1 and all(.host_ip == $ip and .target == 8000
      and (.published | tostring) == "8080" and (.protocol // "tcp") == "tcp")' \
    || fail "web is published once, on WEB_LAN_ADDRESS only (never 0.0.0.0, IPv6 or loopback)" || return 1
  q '.services.web.networks | has("proxy") | not' || fail "web leaves the proxy network" || return 1
  jq -e --arg ip "$swag_ip" --arg lan "$lan_ip" '.services.web.environment.CLASSBEAT_TRUSTED_PROXIES == $ip and $ip != $lan' <<<"$J" >/dev/null \
    || fail "the trusted proxy is PROXY_IP (swag's LAN address), and not this server" || return 1
}

# The owner's real render (#610, #598 review finding 2): compose.prod.yaml with the overlays COMPOSE_FILE
# names, the real .env and every profile, run through the same allow-list (check_keys) and secret-mount
# rules. The value checks of check_common are left out: they compare with the dummies. One addition of
# the owner's own is allowed, exactly as proton-bridge-smtp.md writes it: compose.bridge.yaml's
# read-only bind of ./smtp-ca/bridge.pem into web at /run/smtp-ca/bridge.pem, and the external network
# `bridge-mail` (any name) that web alone joins. Anything else is refused like in the dummy files.
# (Older Compose writes the short bind syntax with `bind: {create_host_path: true}`, newer with `{}`.)
# Nothing here reads a secret file: compose renders a file secret as its path. Only key names and rule
# messages are printed, never a value or a variable name (check_keys).
# shellcheck disable=SC2016  # jq variables, not shell ones
strip_bridge='
  (.secrets.app_secret_key.file // "" | rtrimstr("/secrets/app_secret_key")) as $root
  | (.networks["bridge-mail"] // null) as $nw
  | (.services.web.volumes // []) as $vs
  | if $nw != null and ($nw | type) == "object" and (($nw | keys) - ["name","external","ipam"]) == []
        and $nw.external == true and ($nw.ipam // {}) == {} and ($nw.name | type) == "string" and $nw.name != ""
        and [.services | to_entries[] | select(.value.networks // {} | has("bridge-mail")) | .key] == ["web"]
        and .services.web.networks["bridge-mail"] == null
        and ($vs | length) == 1 and ($vs[0] | type) == "object"
        and (($vs[0] | keys) - ["type","source","target","read_only","bind"]) == []
        and $vs[0].type == "bind" and $root != "" and $vs[0].source == $root + "/smtp-ca/bridge.pem"
        and $vs[0].target == "/run/smtp-ca/bridge.pem" and $vs[0].read_only == true
        and (($vs[0].bind // {}) | . == {} or . == {"create_host_path": true})
    then del(.networks["bridge-mail"], .services.web.networks["bridge-mail"], .services.web.volumes)
    else . end'
check_real() {
  J="$(jq "$strip_bridge" <<<"$J")" || fail "the real render could not be read" || return 1
  check_keys || return 1
  check_secret_mounts || return 1
}

J="$shared"; check_shared || exit 1
J="$loopback"; check_loopback || exit 1
J="$lan"; check_lan || exit 1
J="$defaults"; check_oauth_defaults || exit 1
