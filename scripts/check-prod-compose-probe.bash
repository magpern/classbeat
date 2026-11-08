# shellcheck shell=bash
# --probe: every service reads every secret file it mounts.
# Sourced by check-prod-compose.sh, never run on its own; it shares that script's shell, variables and functions. Part of the check-prod-compose split (#763): no check lives here that is not in the original.
# --- The secret files, as every service sees them (--probe) --------------------------------------
# What the runbook did by hand: for each service that mounts secrets, run the pulled image with a
# shell that asks the kernel (`test -r`, nothing is opened or printed) about every file in
# /run/secrets. Output: "<service>: <file> OK|FAIL", never a value. Any FAIL, or a service that
# prints nothing, fails the check.
probe_verdict() {   # probe_verdict SERVICE OUTPUT
  local svc="$1" out="$2" line n=0 bad=0
  while IFS= read -r line; do
    [[ -n "$line" ]] || continue
    if [[ "$line" =~ ^[A-Za-z0-9_.-]+\ (OK|FAIL)$ ]]; then
      echo "$svc: $line"; n=$((n + 1))
      [[ "${line##* }" == OK ]] || bad=1
    else
      echo "check-prod-compose: $svc: unexpected probe output (not a secret file line)" >&2; bad=1
    fi
  done <<<"$out"
  (( n > 0 )) || fail "$svc: the probe listed no secret file (is the image pulled? docker compose pull)" || return 1
  (( bad == 0 )) || fail "$svc: a secret file is not readable there: fix the group and mode (runbook section 5), then try again" || return 1
}
probe_secrets() {
  local svcs svc out rc=0
  mapfile -t svcs < <(docker compose config --format json 2>/dev/null | jq -r '.services | to_entries[]
    | select((.value.secrets // []) | length > 0) | .key')
  (( ${#svcs[@]} > 0 )) || { fail "no service mounts a secret (wrong COMPOSE_FILE?)"; return 1; }
  for svc in "${svcs[@]}"; do
    out="$(docker compose run --rm --no-deps -T --entrypoint sh "$svc" -c \
      'for f in /run/secrets/*; do if test -r "$f"; then echo "${f##*/} OK"; else echo "${f##*/} FAIL"; fi; done' \
      2>/dev/null)" \
      || { fail "$svc: could not run the pulled image (docker compose pull first? Compose's message is not printed: it can quote a line of a secret file)"; return 1; }
    probe_verdict "$svc" "$out" || rc=1
  done
  return "$rc"
}
if (( probe )); then
  ( restore_real_env; probe_secrets ) || exit 1
  owner_note="$owner_note; every service reads every secret it mounts"
fi
