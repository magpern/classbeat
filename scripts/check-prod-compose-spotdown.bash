# shellcheck shell=bash
# spotdown's overlay (compose.prod.spotdown.yaml, ADR 0040 section 10, #1481): all of it or none of it.
# Sourced by check-prod-compose.sh, never run on its own; it shares that script's shell, variables and functions.
# Called by check_common on every render. A render with no spotdown piece (no spotdown-* service,
# network or volume, no spotdown_service_key, no CLASSBEAT_SPOTDOWN_URL) passes untouched: that is
# compose.prod.yaml alone, exactly as before. A render with any piece must have the whole overlay:
# three services on internal networks only, no port, the service key on classbeat's worker and
# spotdown-api only (never web), a pinned spotdown image (never `latest`), ADR 0026 stage 1 ceilings,
# no capability, no-new-privileges and an explicit non-root user on each, and YouTube and the `url`
# source off.
spotdown_services='["spotdown-api","spotdown-redis","spotdown-worker"]'
# shellcheck disable=SC2016  # jq variables, not shell ones
check_spotdown_parts() {
  local present
  present="$(jq -r '
      [(.services // {} | keys[] | select(startswith("spotdown"))),
       (.networks // {} | keys[] | select(startswith("spotdown"))),
       (.volumes // {} | keys[] | select(startswith("spotdown"))),
       (.secrets // {} | keys[] | select(startswith("spotdown"))),
       (.services[] | .secrets // [] | .[] | .source | select(startswith("spotdown"))),
       (.services[] | .environment // {} | keys[] | select(startswith("CLASSBEAT_SPOTDOWN_")))]
      | length' <<<"$J")" || fail "the rendered file could not be read for spotdown" || return 1
  (( present == 0 )) && return 0
  check_spotdown || return 1
}
# shellcheck disable=SC2016  # jq variables, not shell ones
check_spotdown() {
  q --argjson s "$spotdown_services" '[.services | keys[] | select(startswith("spotdown"))] == $s' \
    || fail "spotdown is the three services spotdown-api, spotdown-worker and spotdown-redis, all of them" || return 1
  # Never `latest`, never a digest-less floating tag: a SemVer release, the same on api and worker.
  q '[.services["spotdown-api"].image, .services["spotdown-worker"].image]
     | all(test("^ghcr\\.io/magpern/spotdown:[0-9]+\\.[0-9]+\\.[0-9]+(-[0-9A-Za-z.]+)?$")) and .[0] == .[1]' \
    || fail "spotdown-api and spotdown-worker run ghcr.io/magpern/spotdown:\$SPOTDOWN_VERSION, a release tag (X.Y.Z, never latest)" || return 1
  q '.services["spotdown-redis"].image | test("^redis:[0-9]+\\.[0-9]+\\.[0-9]+-alpine$")' \
    || fail "spotdown-redis runs a pinned redis:X.Y.Z-alpine" || return 1
  # Internal networks only, and who joins which: the worker reaches spotdown-api and nothing else.
  q '[.networks.spotdown, .networks["spotdown-internal"]] | all(. != null and .internal == true)' \
    || fail "the spotdown and spotdown-internal networks are internal (no route out)" || return 1
  q '[.services | to_entries[] | select(.value.networks // {} | has("spotdown")) | .key] | sort == ["spotdown-api","worker"]' \
    || fail "only classbeat's worker and spotdown-api join the spotdown network (never web)" || return 1
  q --argjson s "$spotdown_services" '[.services | to_entries[] | select(.value.networks // {} | has("spotdown-internal")) | .key] | sort == $s' \
    || fail "only the three spotdown services join spotdown-internal" || return 1
  q '(.services["spotdown-api"].networks | keys) == ["spotdown","spotdown-internal"]
     and (.services["spotdown-worker"].networks | keys) == ["spotdown-internal"]
     and (.services["spotdown-redis"].networks | keys) == ["spotdown-internal"]' \
    || fail "spotdown-api joins spotdown and spotdown-internal, its worker and Redis spotdown-internal only" || return 1
  # The one service key (classbeat.spotdown.key_policy): classbeat's worker and spotdown-api only.
  q '[.services | to_entries[] | select(.value.secrets // [] | map(.source) | index("spotdown_service_key")) | .key] | sort == ["spotdown-api","worker"]' \
    || fail "spotdown_service_key belongs to classbeat's worker and spotdown-api only (never web)" || return 1
  q '(.services["spotdown-api"].secrets | map(.source)) == ["spotdown_service_key"]
     and ([.services["spotdown-worker"], .services["spotdown-redis"]] | all((.secrets // []) == []))' \
    || fail "spotdown-api mounts the service key and nothing else; spotdown-worker and Redis mount no secret" || return 1
  q '.services["spotdown-api"].environment.SERVICE_KEY_FILE == "/run/secrets/spotdown_service_key"' \
    || fail "spotdown-api reads the key from /run/secrets/spotdown_service_key" || return 1
  q '[.services | to_entries[] | select(.value.environment // {} | keys | any(startswith("CLASSBEAT_SPOTDOWN_"))) | .key] == ["worker"]
     and (.services.worker.environment | with_entries(select(.key | startswith("CLASSBEAT_SPOTDOWN_"))))
       == {"CLASSBEAT_SPOTDOWN_URL": "http://spotdown-api:8000"}' \
    || fail "CLASSBEAT_SPOTDOWN_URL is http://spotdown-api:8000, on classbeat's worker only" || return 1
  # Service mode, no UI, the queue on its own Redis; the url source and YouTube off (no route out).
  q '[.services["spotdown-api"], .services["spotdown-worker"] | .environment
      | .SERVICE_MODE == "true" and .SERVE_UI == "false" and .DATA_DIR == "/app/data"
        and .REDIS_URL == "redis://spotdown-redis:6379/0" and .URL_ALLOWED_HOSTS == ""
        and .ALLOW_YTDLP_SOURCING == "false"] | all' \
    || fail "spotdown runs in service mode without UI, with the url source and YouTube sourcing off" || return 1
  # Each service: no capability, no-new-privileges, a named non-root user, a ceiling, log rotation.
  q --argjson s "$spotdown_services" '[.services[$s[]] | (.cap_drop // []) == ["ALL"] and (.security_opt // []) == ["no-new-privileges:true"]] | all' \
    || fail "every spotdown service drops every capability and sets no-new-privileges" || return 1
  q --argjson s "$spotdown_services" '[.services[$s[]] | .user // "" | tostring | test("^[1-9][0-9]*:[1-9][0-9]*$")] | all' \
    || fail "every spotdown service names a non-root uid:gid" || return 1
  q --argjson s "$spotdown_services" '[.services[$s[]] | (.cpus | tonumber? // 0) > 0 and ((.mem_limit // "") | tostring | test("^[1-9][0-9]*$"))] | all' \
    || fail "every spotdown service keeps a cpus and a mem_limit ceiling (ADR 0026 stage 1)" || return 1
  q '[.services["spotdown-api"], .services["spotdown-redis"]] | all(.healthcheck.test != null)' \
    || fail "spotdown-api and spotdown-redis need their healthchecks" || return 1
  # spotdown's data on its named volumes, nothing else.
  q '(.volumes | keys) == ["db-data","spotdown-data","spotdown-redis"]
     and ([.services["spotdown-api"], .services["spotdown-worker"]] | all(.volumes == [{"type":"volume","source":"spotdown-data","target":"/app/data","volume":{}}]))
     and .services["spotdown-redis"].volumes == [{"type":"volume","source":"spotdown-redis","target":"/data","volume":{}}]' \
    || fail "spotdown's data is the volumes spotdown-data (/app/data) and spotdown-redis (/data), nothing else" || return 1
  q --argjson s "$spotdown_services" '[.services | to_entries[] | select(.value.volumes // [] | map(.source // "") | any(startswith("spotdown"))) | .key] | sort == $s' \
    || fail "only the spotdown services mount spotdown's volumes" || return 1
}
# compose.prod.yaml with compose.prod.spotdown.yaml: the shared variant's rules, and spotdown is there.
check_withspotdown() {
  check_shared || return 1
  check_spotdown || return 1
}

# In an install folder whose COMPOSE_FILE names the overlay: spotdown-api refuses to start without a
# key (and classbeat's worker would treat an empty file as "no spotdown"), so an empty or missing
# secrets/spotdown_service_key is a failure here, before `up` (runbook section 5d). Size only (`-s`):
# the file is never opened and nothing of it is printed. Without the overlay nothing is asked.
check_spotdown_key() {   # check_spotdown_key RENDERED_JSON SECRETS_DIR
  local on
  on="$(jq -r '.services | has("spotdown-api")' <<<"$1")" || fail "the real render could not be read (spotdown)" || return 1
  [[ "$on" == true ]] || return 0
  [[ -s "$2/spotdown_service_key" ]] \
    || fail "compose.prod.spotdown.yaml is in COMPOSE_FILE but secrets/spotdown_service_key is missing or empty, so spotdown-api would refuse to start: write the key into it (runbook section 5d) or take the overlay out of COMPOSE_FILE" \
    || return 1
}
if [[ $mode == repo ]]; then   # the self-test; the install-folder cases are in scripts/test-install-check.sh
  sk_dir="$tmp/spotdown-key"; mkdir "$sk_dir"
  sk_canary="spotdown-canary-$RANDOM$RANDOM"
  sk_expect() {   # sk_expect WANT(ok|refused) SPOTDOWN(on|off) KEY(none|empty|full)
    local want="$1" j='{"services":{"worker":{}}}' rc=0 out
    [[ "$2" == off ]] || j='{"services":{"worker":{},"spotdown-api":{}}}'
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
fi
