# shellcheck shell=bash
# spotdown's link (compose.prod.spotdown.yaml, ADR 0040 section 10 and its amendment of 2026-11-25, #1526).
# Sourced by check-prod-compose.sh, never run on its own; it shares that script's shell, variables and functions.
# classbeat never runs spotdown: it is its own project, installed from its own release, and classbeat's
# worker calls it at CLASSBEAT_SPOTDOWN_URL. Two rules, both called by check_common on every render:
#   check_no_spotdown_hosted  every production render, overlay or not: no service named spotdown*, no
#                             service running a spotdown image (whatever its name), no volume named
#                             spotdown*, no spotdown* network but the one external `spotdown` network;
#   check_spotdown_parts      a render with any piece of the link must have all of it (check_spotdown): the
#                             URL on the worker only, the key file mounted by the worker only, the external
#                             network joined by the worker only. No piece at all is compose.prod.yaml alone.
# check_spotdown_key (install mode) asks for a filled key file when the overlay is in the render.
# shellcheck disable=SC2016  # jq variables, not shell ones
check_no_spotdown_hosted() {
  q '[.services // {} | keys[] | select(startswith("spotdown"))] | length == 0' \
    || fail "classbeat never runs spotdown: no service named spotdown* (install spotdown from its own release, runbook section 5d)" || return 1
  q '[.services // {} | to_entries[] | select((.value.image // "") | tostring
        | test("^ghcr\\.io/magpern/spotdown|(^|/)spotdown[^/]*$"; "i"))] | length == 0' \
    || fail "classbeat never runs spotdown: no service runs a spotdown image, whatever the service is called (runbook section 5d)" || return 1
  q '([.volumes // {} | keys[] | select(startswith("spotdown"))]
      + [.services // {} | .[] | .volumes // [] | .[] | (.source // "") | select(startswith("spotdown"))]) | length == 0' \
    || fail "classbeat never runs spotdown: no volume named spotdown* (spotdown keeps its own data, runbook section 5d)" || return 1
  q '[.networks // {} | to_entries[] | select(.key | startswith("spotdown"))
      | select(.key != "spotdown" or (.value.external // false) != true)] | length == 0' \
    || fail "the only spotdown network is the external one spotdown created (networks.spotdown with external: true), never one of classbeat's own" || return 1
}
check_spotdown_parts() {   # check_spotdown_parts [real]  (real: the owner's render, any URL)
  local present
  present="$(jq -r '
      [(.networks // {} | keys[] | select(startswith("spotdown"))),
       (.secrets // {} | keys[] | select(startswith("spotdown"))),
       (.services[] | .networks // {} | keys[] | select(startswith("spotdown"))),
       (.services[] | .secrets // [] | .[] | .source | select(startswith("spotdown"))),
       (.services[] | .environment // {} | keys[] | select(startswith("CLASSBEAT_SPOTDOWN_")))]
      | length' <<<"$J")" || fail "the rendered file could not be read for spotdown" || return 1
  (( present == 0 )) && return 0
  check_spotdown "${1:-}" || return 1
}
# The link, whole. With "real" (the owner's own render) the URL is the owner's; without it (the dummy
# renders) it is the default. The key file is secrets/<its name> by check_secret_mounts like every secret.
check_spotdown() {   # check_spotdown [real]
  local real="${1:-}"
  q '(.networks.spotdown // null) as $n | $n != null and ($n | type) == "object" and (($n | keys) - ["name","external","ipam"]) == []
     and ($n.ipam // {}) == {} and $n.external == true and $n.name == "spotdown"' \
    || fail "networks.spotdown is the external network named spotdown that spotdown's own runbook creates (external: true, no other key)" || return 1
  q '[.services | to_entries[] | select(.value.networks // {} | has("spotdown")) | .key] == ["worker"]' \
    || fail "only classbeat's worker joins the spotdown network (never web or another service)" || return 1
  q '(.services.worker.networks | keys) == ["backend","egress","spotdown"]' \
    || fail "classbeat's worker joins backend, egress and spotdown, nothing else" || return 1
  q '.secrets.spotdown_service_key != null' \
    || fail "the spotdown overlay declares the secret spotdown_service_key" || return 1
  q '[.services | to_entries[] | select(.value.secrets // [] | map(.source) | index("spotdown_service_key")) | .key] == ["worker"]' \
    || fail "spotdown_service_key belongs to classbeat's worker only (never web)" || return 1
  q '[.services | to_entries[] | select(.value.environment // {} | keys | any(startswith("CLASSBEAT_SPOTDOWN_"))) | .key] == ["worker"]
     and (.services.worker.environment | with_entries(select(.key | startswith("CLASSBEAT_SPOTDOWN_"))) | keys) == ["CLASSBEAT_SPOTDOWN_URL"]
     and (.services.worker.environment.CLASSBEAT_SPOTDOWN_URL | type == "string" and length > 0)' \
    || fail "CLASSBEAT_SPOTDOWN_URL is set (SPOTDOWN_URL, never empty), on classbeat's worker only" || return 1
  [[ -n "$real" ]] || q '.services.worker.environment.CLASSBEAT_SPOTDOWN_URL == "http://spotdown-api:8000"' \
    || fail "CLASSBEAT_SPOTDOWN_URL defaults to http://spotdown-api:8000" || return 1
}
# compose.prod.yaml with compose.prod.spotdown.yaml: the shared variant's rules, and the link is there.
check_withspotdown() {
  check_shared || return 1
  check_spotdown || return 1
}

# In an install folder whose COMPOSE_FILE names the overlay: an empty or missing secrets/spotdown_service_key
# would make the worker treat spotdown as off (spotdown_available), so it is a failure here, before `up`
# (runbook section 5d). Size only (`-s`): the file is never opened and nothing of it is printed. Its group
# and mode are check_secret_files's, like every secret's. Without the overlay nothing is asked.
check_spotdown_key() {   # check_spotdown_key RENDERED_JSON SECRETS_DIR
  local on
  on="$(jq -r '(.secrets // {}) | has("spotdown_service_key")' <<<"$1")" || fail "the real render could not be read (spotdown)" || return 1
  [[ "$on" == true ]] || return 0
  [[ -s "$2/spotdown_service_key" ]] \
    || fail "compose.prod.spotdown.yaml is in COMPOSE_FILE but secrets/spotdown_service_key is missing or empty, so the worker would treat spotdown as off: copy spotdown's key into it (runbook section 5d) or take the overlay out of COMPOSE_FILE" \
    || return 1
}
