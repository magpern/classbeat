# shellcheck shell=bash
# The real secret files on the server: group, mode and folder (#511).
# Sourced by check-prod-compose.sh, never run on its own; it shares that script's shell, variables and functions. Part of the check-prod-compose split (#763): no check lives here that is not in the original.
# --- The real secret files on the server (#511) ---------------------------------------------------
# Only the metadata of each file compose.prod.yaml names (stat: type, group number, mode); a file is
# never opened, and nothing but its name, mode and group number is printed. Each must be a regular
# file (not a link), group secrets_gid, readable by the group, not writable by it, nothing for
# others; the folder gives the group and others nothing (mode & 077 == 0, i.e. 2700; #517). Runs on
# the server: when the env file's COMPOSE_FILE names compose.prod.yaml (runbook section 5), or when
# PROD_CHECK_SECRETS_DIR names a folder to check.
# Every failure says what to run; the steps that need sudo are marked (an owner outside the group
# cannot set the group or the setgid bit on the folder, which is why the first install needs sudo
# once, runbook section 5).
check_secret_files() {   # check_secret_files DIR GID NAME...
  local d="$1" gid="$2" n st g a m ds
  shift 2
  [[ -d "$d" && ! -L "$d" ]] || fail "$d is not a folder (runbook section 5: mkdir secrets, then the one-time sudo step)" || return 1
  ds="$(stat -c '%g %a' "$d")" || fail "$d: stat failed" || return 1
  g="${ds% *}"; a="${ds#* }"
  (( (8#$a & 8#077) == 0 )) || fail "$d: mode $a gives the group or others access: chmod 2700 $d" || return 1
  [[ "$g" == "$gid" ]] \
    || fail "$d: group $g, must be $gid. One-time, needs sudo: sudo chgrp $gid $d && sudo chmod 2700 $d (runbook section 5)" || return 1
  (( (8#$a & 8#2000) != 0 )) \
    || fail "$d: mode $a has no setgid bit, so new files would not get group $gid. One-time, needs sudo: sudo chmod 2700 $d" || return 1
  for n in "$@"; do
    [[ ! -L "$d/$n" ]] || fail "secrets/$n is a symbolic link: replace it with a regular file" || return 1
    [[ -f "$d/$n" ]] || fail "secrets/$n is missing: ( umask 027; install -m 640 /dev/null secrets/$n ) for an empty one (runbook section 5)" || return 1
    st="$(stat -c '%g %a' "$d/$n")" || fail "secrets/$n: stat failed" || return 1
    g="${st% *}"; a="${st#* }"; m=$(( 8#$a ))
    [[ "$g" == "$gid" ]] || fail "secrets/$n: group $g, must be $gid: sudo chgrp $gid secrets/$n (a file made in the folder gets it by itself)" || return 1
    (( (m & 8#007) == 0 )) || fail "secrets/$n: mode $a is readable or writable by others: chmod 640 secrets/$n" || return 1
    (( (m & 8#040) != 0 )) || fail "secrets/$n: mode $a, the group must read it: chmod 640 secrets/$n" || return 1
    (( (m & 8#030) == 0 )) || fail "secrets/$n: mode $a, the group may only read it: chmod 640 secrets/$n" || return 1
  done
}
# No account on the host has the secrets group (by number): it is for the containers only.
check_secrets_group() {   # check_secrets_group GID "$(getent group GID)" "$(getent passwd)"
  local gid="$1" grp="$2" pw="$3"
  [[ -z "$(cut -s -d: -f4 <<<"$grp")" ]] || fail "group $gid has members on this host (it must have none)" || return 1
  ! awk -F: -v g="$gid" '$4 == g { found = 1 } END { exit !found }' <<<"$pw" \
    || fail "an account on this host has primary group $gid (it must have none)" || return 1
}
secret_names=()
mapfile -t secret_names < <(jq -r '.secrets | keys[]' <<<"$shared")
(( ${#secret_names[@]} >= 15 )) || { fail "compose.prod.yaml names fewer secrets than expected"; exit 1; }
server_secrets=""
if [[ -n "${PROD_CHECK_SECRETS_DIR:-}" ]]; then
  server_secrets="$PROD_CHECK_SECRETS_DIR"
elif [[ "$mode" == install ]] || { [[ -f "$env_file" ]] && grep -Eq '^[[:space:]]*COMPOSE_FILE=[^#]*compose\.prod\.yaml' "$env_file"; }; then
  server_secrets="$root/secrets"
fi
if [[ -n "$server_secrets" ]]; then
  check_secret_files "$server_secrets" "$secrets_gid" "${secret_names[@]}" || exit 1
  if command -v getent >/dev/null; then
    check_secrets_group "$secrets_gid" "$(getent group "$secrets_gid" || true)" "$(getent passwd || true)" || exit 1
    [[ -n "$(getent group "$secrets_gid" || true)" ]] \
      || echo "check-prod-compose: note: group $secrets_gid has no name here; the number works anyway. To reserve it: sudo groupadd --system --gid $secrets_gid classbeat-secrets" >&2
  fi
  owner_note="$owner_note; secrets/ files are group $secrets_gid, mode 640 or stricter"
else
  owner_note="$owner_note; no server secrets/ checked (not a compose.prod.yaml checkout)"
fi
if [[ $mode == repo ]]; then   # the self-tests and the checkout-only checks (ADR 0020)
# Self-test with throwaway empty files of this user's own group (no root needed to make them).
st_dir="$tmp/secret-files"
mygid="$(id -g)"
reset_secret_files() {
  rm -rf "$st_dir"; mkdir "$st_dir"; chmod 2700 "$st_dir"
  local n; for n in "${secret_names[@]}"; do install -m 640 /dev/null "$st_dir/$n"; done
  chgrp "$mygid" "$st_dir"/* 2>/dev/null || true
}
reset_secret_files
# Git Bash on NTFS has no Unix modes (chmod changes nothing): there the file self-test is skipped.
chmod 600 "$st_dir/app_secret_key"
if [[ "$(stat -c '%a' "$st_dir/app_secret_key")" != 600 ]]; then
  echo "check-prod-compose: no Unix file modes here: the secret file self-test was skipped" >&2
else
reset_secret_files
check_secret_files "$st_dir" "$mygid" "${secret_names[@]}" \
  || { echo "check-prod-compose: self-test: refused good secret files" >&2; exit 1; }
chmod 440 "$st_dir/app_secret_key"
check_secret_files "$st_dir" "$mygid" "${secret_names[@]}" \
  || { echo "check-prod-compose: self-test: refused a 440 secret file" >&2; exit 1; }
# shellcheck disable=SC2016  # each mutation is eval'd below, with $st_dir set
for m in 'chmod 644 "$st_dir/app_secret_key"' 'chmod 600 "$st_dir/connection_kek"' 'chmod 604 "$st_dir/smtp_password"' \
  'chmod 660 "$st_dir/database_password"' 'chmod 650 "$st_dir/postgres_password"' 'chmod 641 "$st_dir/anthropic_api_key"' \
  'rm "$st_dir/oauth_google_client_secret"' 'chmod 755 "$st_dir"' 'chmod 2701 "$st_dir"' 'chmod 2750 "$st_dir"' 'chmod 2710 "$st_dir"' 'chmod 2770 "$st_dir"' \
  'rm "$st_dir/swedebeat_app"; install -m 640 /dev/null "$tmp/outside"; ln -s "$tmp/outside" "$st_dir/swedebeat_app"' \
  'rm "$st_dir/deepseek_api_key"; mkdir -m 750 "$st_dir/deepseek_api_key"'; do
  reset_secret_files
  eval "$m"
  if (check_secret_files "$st_dir" "$mygid" "${secret_names[@]}" 2>/dev/null); then
    echo "check-prod-compose: self-test: accepted unsafe secret files: $m" >&2; exit 1
  fi
done
reset_secret_files
if (check_secret_files "$st_dir" "$((mygid + 1))" "${secret_names[@]}" 2>/dev/null); then
  echo "check-prod-compose: self-test: accepted secret files of another group" >&2; exit 1
fi
fi
check_secrets_group 10010 'classbeat-secrets:x:10010:' $'root:x:0:0::/root:/bin/sh\ndeploy:x:1000:1000::/home/deploy:/bin/bash' \
  || { echo "check-prod-compose: self-test: refused a memberless secrets group" >&2; exit 1; }
check_secrets_group 10010 '' 'root:x:0:0::/root:/bin/sh' \
  || { echo "check-prod-compose: self-test: refused a host without that group" >&2; exit 1; }
if (check_secrets_group 10010 'classbeat-secrets:x:10010:deploy' 'root:x:0:0::/root:/bin/sh' 2>/dev/null) \
  || (check_secrets_group 10010 '' $'svc:x:1001:10010::/home/svc:/bin/sh' 2>/dev/null); then
  echo "check-prod-compose: self-test: accepted a secrets group with an account in it" >&2; exit 1
fi

fi
