# shellcheck shell=bash
# Turnstile on needs its keys: the real render and secrets/turnstile_secret_key (ADR 0025 Amendment 2, #798).
# Sourced by check-prod-compose.sh, never run on its own; it shares that script's shell, variables and functions.
# Since #792 compose.prod.yaml turns Turnstile on unless .env says TURNSTILE_ENABLED=false, and `web` refuses to
# start with it on and no TURNSTILE_SITE_KEY or an empty secret file. In an install folder that is a failure
# here, before `up`, like every other install check (runbook sections 5b and 11). The secret file is only
# size-tested (`-s`): it is never opened, and neither it nor the site key is ever printed. A file of only blanks or
# a newline has a size and passes; telling it apart would mean reading it, which this check never does.
# check_turnstile_keys RENDERED_JSON SECRETS_DIR
check_turnstile_keys() {
  local j="$1" dir="$2" site
  # The comparison is inside jq: `$(...)` strips trailing newlines, so a rendered "false\n" would read as off
  # here while the app's boolean parse rejects it. jq -e exits 0 only for true: off needs the exact spelling.
  # jq -e judges the LAST output, so the input must stay one JSON document (`docker compose config --format json`
  # prints one); a second document saying "off" after the first would pass as off.
  if jq -e '(.services.web.environment.CLASSBEAT_TURNSTILE_ENABLED // "") | tostring | ascii_downcase
      | IN("false","0","no","off","f","n")' <<<"$j" >/dev/null; then
    return 0
  else
    local rc=$?
    # 1 = not an off spelling (on, or web's own parse fails; the keys are needed); anything else = jq failed.
    [[ $rc == 1 ]] || fail "the real render could not be read (Turnstile)" || return 1
  fi
  site="$(jq -r '.services.web.environment.CLASSBEAT_TURNSTILE_SITE_KEY // ""' <<<"$j")"
  [[ -n "${site//[[:space:]]/}" ]] \
    || fail "Turnstile is on (the default) but TURNSTILE_SITE_KEY in .env is empty, so web would refuse to start: set it (runbook section 5b) or put TURNSTILE_ENABLED=false in .env until the widget and keys exist" \
    || return 1
  [[ -s "$dir/turnstile_secret_key" ]] \
    || fail "Turnstile is on (the default) but secrets/turnstile_secret_key is missing or empty, so web would refuse to start: write the secret key into it (runbook section 5b) or put TURNSTILE_ENABLED=false in .env until the widget and keys exist" \
    || return 1
}
if [[ $mode == install ]]; then
  check_turnstile_keys "$real_json" "$server_secrets" || exit 1
  owner_note="$owner_note; Turnstile is off, or its site key and secret file are set"
fi

if [[ $mode == repo ]]; then   # the self-tests (ADR 0020); the install-folder cases are in scripts/test-install-check.sh
ts_dir="$tmp/turnstile-files"; mkdir "$ts_dir"
ts_canary="turnstile-canary-$RANDOM$RANDOM"
ts_json() { jq -n --arg e "${1//\\n/$'\n'}" --arg k "$2" '{services:{web:{environment:{CLASSBEAT_TURNSTILE_ENABLED:$e,CLASSBEAT_TURNSTILE_SITE_KEY:$k}}}}'; }
ts_expect() {   # ts_expect WANT(ok|refused) ENABLED SITE_KEY SECRET(none|empty|full)
  local want="$1" rc=0 out
  rm -f "$ts_dir/turnstile_secret_key"
  case "$4" in empty) : > "$ts_dir/turnstile_secret_key" ;; full) printf '%s' "$ts_canary" > "$ts_dir/turnstile_secret_key" ;; esac
  out="$( (check_turnstile_keys "$(ts_json "$2" "$3")" "$ts_dir") 2>&1)" || rc=$?
  if [[ "$want" == ok && "$rc" != 0 ]] || [[ "$want" == refused && "$rc" == 0 ]]; then
    echo "check-prod-compose: self-test: Turnstile '$2' site key '${3:+set}' secret $4: wanted $want, rc $rc" >&2; exit 1
  fi
  if [[ "$want" == refused ]]; then
    grep -q 'section 5b' <<<"$out" && grep -q 'TURNSTILE_ENABLED=false' <<<"$out" \
      || { echo "check-prod-compose: self-test: the Turnstile refusal does not point to section 5b and TURNSTILE_ENABLED=false" >&2; exit 1; }
  fi
  if grep -qF -- "$ts_canary" <<<"$out" || grep -qF -- 0x4AAAAAAcheck <<<"$out"; then
    echo "check-prod-compose: self-test: the Turnstile check printed a key" >&2; exit 1
  fi
}
ts_expect ok true 0x4AAAAAAcheck full
ts_expect ok TRUE 0x4AAAAAAcheck full
ts_expect refused true '' full
ts_expect refused true '  ' full
ts_expect refused true 0x4AAAAAAcheck empty
ts_expect refused true 0x4AAAAAAcheck none
ts_expect refused true '' none
ts_expect refused 1 '' full
ts_expect ok false '' none
ts_expect ok false '' empty
ts_expect ok False '' none
ts_expect ok 0 '' none
ts_expect refused 'false\n' 0x4AAAAAAcheck none
ts_expect refused 'false ' 0x4AAAAAAcheck none
ts_expect ok FALSE '' none
# An empty ENABLED is not an off spelling (compose renders `${TURNSTILE_ENABLED:-true}`, so it never is empty;
# a missing key would be refused too): the keys are needed.
ts_expect refused '' 0x4AAAAAAcheck none
# Input jq cannot read fails closed with its own message (jq exits other than 1).
if (check_turnstile_keys 'not json' "$ts_dir") >"$tmp/ts-bad-json" 2>&1 || ! grep -q 'could not be read (Turnstile)' "$tmp/ts-bad-json"; then
  echo "check-prod-compose: self-test: unreadable render JSON was not refused as unreadable (Turnstile)" >&2; exit 1
fi
fi
