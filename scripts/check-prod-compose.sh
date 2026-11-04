#!/usr/bin/env bash
# Checks the production compose files and the sample swag site config with dummy values (#341, #492).
# No secret is ever read, nothing contacted but the local Docker daemon (and its registry for the
# nginx image).
#   scripts/check-prod-compose.sh        (needs docker, jq; run from anywhere)
# 1. `docker compose config` accepts compose.prod.yaml alone (swag on a shared Docker network) and
#    with compose.prod.loopback.yaml (a proxy on the host, web on 127.0.0.1);
# 2. the invariants of the production stack hold for both (with the OAuth settings of #507: the
#    client ids only from their variables, the client secrets as files on web only, never a value
#    in the rendered files; #511: every service that mounts a secret has group 10010 and a non-root
#    user, each secret is secrets/<its own name>, and the known ways around a secret mount are
#    refused), and compose.prod.yaml's defaults leave every OAuth client id empty;
# (the SQL the database services run is in the release's classbeat-db image: no bind mounts, ADR 0020)
# 3. a self-test: each unsafe mutation of the rendered files must fail step 2 (fails closed);
# 4. the owner's real PROXY_IP, when one is set (their shell, else PROD_CHECK_ENV_FILE, default the
#    checkout's .env): compose.prod.yaml rendered with it must trust one private IPv4 address (#495);
# 4b. that env file and the compose files hold no *CLIENT_SECRET* variable (a file under secrets/ only);
# 4c. on the server (that env file's COMPOSE_FILE names compose.prod.yaml, or PROD_CHECK_SECRETS_DIR
#    is set): each real secret file is a regular file of group 10010, mode 640 or stricter, in a
#    folder closed to others, and no host account has that group (#511; metadata only, no content);
# 5. the runbook's `docker network create` keeps swag's fixed address outside Docker's --ip-range;
# 6. `nginx -t` accepts docs/runbooks/swag-classbeat.subdomain.conf, and the lines the app's
#    client address, body cap, SSE stream and headers rely on are in it (X-Forwarded-For set
#    exactly once);
# and a self-test for 4, 4b, 4c, 5 and 6 as for 2. On the server: run it in /opt/classbeat after writing
# .env (runbook section 9).
#
# What it is: a best-effort lint of the owner's own committed, reviewed compose files against honest
# mistakes. It is NOT a security boundary against someone who can edit those files (they could run
# anything). The boundary for the secrets is on the server: the files' mode and group (4c, runbook
# section 5) and which services mount which secret. Its compose rules list the known channels one by
# one; a default-deny key allow-list is follow-up #517.
# Two places it runs (ADR 0020, #566):
#   a checkout (CI and developers): everything above, including the self-tests, the runbook and
#     the swag sample (needs docs/ and the nginx image);
#   an install folder (the released script next to compose.prod.yaml, no docs/): the pre-deploy
#     check on the server, `./check-prod-compose.sh [--probe]` from /opt/classbeat. It checks the
#     files there with dummy values (so a local edit of a compose file is caught), renders the real
#     .env, checks the real PROXY_IP, the env file and the real secret files and their folder, and
#     with --probe runs, per service that mounts secrets, a readability test of every secret file
#     from the pulled image (prints "<service>: <file> OK|FAIL", never a value; needs
#     `docker compose pull` first). `--install` forces that mode in a checkout.
set -euo pipefail
export MSYS_NO_PATHCONV=1   # Git Bash: paths below are for the container, never converted

mode=auto; probe=0; rollback_old=""
while (( $# )); do
  case "$1" in
    --install) mode=install ;;
    --probe) mode=install; probe=1 ;;
    --rollback-check) mode=install; shift; rollback_old="${1:-}"
      [[ "$rollback_old" =~ ^[0-9A-Za-z][0-9A-Za-z.+-]*$ ]] \
        || { echo "usage: check-prod-compose.sh --rollback-check OLD_VERSION (e.g. 1.2.3)" >&2; exit 2; } ;;
    *) echo "usage: check-prod-compose.sh [--install] [--probe] [--rollback-check OLD_VERSION]" >&2; exit 2 ;;
  esac
  shift
done
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "$here/../compose.prod.yaml" && -d "$here/../docs/runbooks" ]]; then
  root="$(cd "$here/.." && pwd)"; layout=checkout
else
  root="$here"; layout=install
fi
[[ "$mode" != auto ]] || { [[ "$layout" == install ]] && mode=install || mode=repo; }
[[ -f "$root/compose.prod.yaml" && -f "$root/compose.prod.loopback.yaml" && -f "$root/compose.prod.lan.yaml" ]] \
  || { echo "check-prod-compose: compose.prod.yaml, compose.prod.loopback.yaml and compose.prod.lan.yaml must be next to this script" >&2; exit 1; }
cd "$root"

# --- Rollback test, no clone (ADR 0020 decision 7) -------------------------------------------------
# Going back to release OLD is safe only if the live database is at a revision OLD knows, i.e. the
# release you leave ran no migration OLD lacks. The database's Alembic revision is read from
# alembic_version; OLD's revisions are the migration files in its image (a revision's id is its file
# name). Unknown revision: OLD's `migrate` would fail ("Can't locate revision"); restore the backup.
rollback_verdict() {   # rollback_verdict OLD "REVISIONS (one per line)" "FILE LISTING (one per line)"
  local old="$1" revs="$2" files="$3" rev n=0
  while IFS= read -r rev; do
    [[ -n "$rev" ]] || continue
    n=$((n + 1))
    [[ "$rev" =~ ^[A-Za-z0-9_]+$ ]] || { echo "check-prod-compose: the database reports an odd revision; not rolling back" >&2; return 1; }
    grep -Fxq -- "$rev.py" <<<"$files" || {
      echo "check-prod-compose: the database is at revision $rev, which release $old does not have: its migrate would fail." >&2
      echo "check-prod-compose: do not set CLASSBEAT_VERSION back; restore the pre-update backup (runbook section 11, Rollback)." >&2
      return 1
    }
  done <<<"$revs"
  (( n > 0 )) || { echo "check-prod-compose: the database has no Alembic revision (alembic_version is empty)" >&2; return 1; }
  echo "rollback check: every database revision is known to release $old: no migration ran that it lacks"
}
rollback_check() {   # rollback_check OLD
  local old="$1" revs files
  revs="$(docker compose exec -T db psql -U postgres -d classbeat -X -At -c 'select version_num from alembic_version' 2>/dev/null)" \
    || { echo "check-prod-compose: cannot read the database revision (is db running? docker compose up -d db)" >&2; return 1; }
  files="$(docker run --rm --entrypoint ls "ghcr.io/magpern/classbeat:$old" /app/migrations/versions 2>/dev/null)" \
    || { echo "check-prod-compose: cannot list the migrations of ghcr.io/magpern/classbeat:$old (docker login, then docker pull it)" >&2; return 1; }
  rollback_verdict "$old" "$revs" "$files"
}
if [[ -n "$rollback_old" ]]; then
  rollback_check "$rollback_old"
  exit $?
fi
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
# Every variable the dummies below replace, as the owner had it: --probe runs compose with these.
dummy_names=(CLASSBEAT_VERSION CLASSBEAT_DOMAIN PROXY_IP SMTP_HOST SMTP_SENDER SMTP_CA_FILE COMPOSE_PROJECT_NAME
  COMPOSE_FILE PROXY_NETWORK WEB_HOST_PORT WEB_LAN_ADDRESS OAUTH_GOOGLE_CLIENT_ID OAUTH_GITHUB_CLIENT_ID OAUTH_MICROSOFT_CLIENT_ID
  OAUTH_VISIBLE_PROVIDERS)
declare -A real_set=() real_val=()
for n in "${dummy_names[@]}"; do
  if [[ -v "$n" ]]; then real_set[$n]=1; real_val[$n]="${!n}"; fi
done
restore_real_env() {
  local n
  for n in "${dummy_names[@]}"; do
    if [[ -n "${real_set[$n]:-}" ]]; then export "$n=${real_val[$n]}"; else unset "$n"; fi
  done
}
if [[ "$mode" == install ]]; then
  # The real settings render with the real .env before the dummies below replace anything.
  [[ -f "$env_file" ]] || { echo "check-prod-compose: no $env_file: cp prod.env.example .env and fill it in (runbook section 5)" >&2; exit 1; }
  real_json="$(docker compose config --format json 2>"$tmp/real-config.log")" \
    || { cat "$tmp/real-config.log" >&2; echo "check-prod-compose: docker compose config fails with your .env (above): set the variables it names" >&2; exit 1; }
fi

# Copies next to dummy secret files, so the checkout's own secrets/ is never read.
cp compose.prod.yaml compose.prod.loopback.yaml compose.prod.lan.yaml "$tmp/"
mkdir "$tmp/secrets"
oauth_providers=(google github microsoft)
for n in postgres_password database_owner_password database_password database_maint_password \
  app_secret_key connection_kek smtp_password anthropic_api_key openai_api_key deepseek_api_key swedebeat_app; do
  : > "$tmp/secrets/$n"
done
# The OAuth client secret files hold a canary (a dummy, never a real secret): it must not appear in
# any rendered file, so compose never inlines a client secret (#507).
oauth_canary="oauth-canary-$RANDOM$RANDOM$RANDOM"
for p in "${oauth_providers[@]}"; do
  printf '%s' "$oauth_canary-$p" > "$tmp/secrets/oauth_${p}_client_secret"
done

swag_ip=172.29.90.10
export CLASSBEAT_VERSION=0.0.0 CLASSBEAT_DOMAIN=classbeat.example.test PROXY_IP="$swag_ip"
export SMTP_HOST=smtp.example.test SMTP_SENDER=noreply@example.test
export SMTP_CA_FILE=/run/smtp-ca/bridge.pem
export COMPOSE_PROJECT_NAME=classbeat-prod-check
unset COMPOSE_FILE PROXY_NETWORK WEB_HOST_PORT
lan_ip=192.168.1.20   # the dummy WEB_LAN_ADDRESS (a private address, not swag's)
export WEB_LAN_ADDRESS="$lan_ip"
dir="$native/${tmp##*/}"
# Every render enables all profiles: a service behind `profiles:` would otherwise be left out of the
# rendered file and so of every check below (#512 review B5).
render() { docker compose "$@" --profile '*' config --format json; }
# compose.prod.yaml's defaults: no OAuth variable set, so every provider is off.
unset OAUTH_GOOGLE_CLIENT_ID OAUTH_GITHUB_CLIENT_ID OAUTH_MICROSOFT_CLIENT_ID OAUTH_VISIBLE_PROVIDERS
defaults="$(render -f "$dir/compose.prod.yaml")"
# Dummy client ids (public values, not secret): web's settings must equal them, so they come from
# the variables and from nothing written into the compose files.
export OAUTH_GOOGLE_CLIENT_ID=check-google-client-id OAUTH_GITHUB_CLIENT_ID=check-github-client-id
export OAUTH_MICROSOFT_CLIENT_ID=check-microsoft-client-id OAUTH_VISIBLE_PROVIDERS=google,github
shared="$(render -f "$dir/compose.prod.yaml")"
loopback="$(render -f "$dir/compose.prod.yaml" -f "$dir/compose.prod.loopback.yaml")"
lan="$(render -f "$dir/compose.prod.yaml" -f "$dir/compose.prod.lan.yaml")"

J=""
fail() { echo "check-prod-compose: $*" >&2; return 1; }
q() { jq -e "$@" <<<"$J" >/dev/null; }

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
  q '[.services.db.image, .services["db-functions"].image] | all(. == "ghcr.io/magpern/classbeat-db:0.0.0")' \
    || fail "db and db-functions run the release's classbeat-db image (ADR 0020)" || return 1
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
  q '.services.web.environment.CLASSBEAT_SOUNDLESS_ENABLED == "false"' \
    || fail "soundless classes must be off by default" || return 1
  q '.services.worker.environment.CLASSBEAT_SOUNDLESS_ENABLED == "false"' \
    || fail "soundless classes must be off by default on the worker too" || return 1
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
  check_oauth || return 1
  check_secret_mounts || return 1
}

# Secret files (#511). Outside Swarm a file secret is a bind mount with the host file's owner and
# mode, so every service that mounts one runs as a non-root user with the group of the files
# (secrets_gid). The postgres image's root entrypoint re-execs as postgres through gosu, which drops
# supplementary groups: a postgres service runs as postgres from the start. Each top-level secret is
# the file secrets/<its own name> (no second name for a file, #508 review), mounted at its own name,
# and nothing reaches secrets/ another way: no bind mount of the folder, a parent or a file in it, no
# configs at all, no post_start/pre_stop hooks, no volumes_from, no shared pid or ipc namespace, no named volume with driver
# options, and no command, entrypoint or healthcheck that names /run/secrets or secrets/ (a plain
# text match against honest mistakes; a reviewed compose file is the guard against intent).
secrets_gid=10010
# shellcheck disable=SC2016  # jq variables, not shell ones
check_secret_mounts() {
  jq -e --arg g "$secrets_gid" '[.services[] | select((.secrets // []) | length > 0)
      | (.group_add // []) | map(tostring) | index($g)] | all(. != null)' <<<"$J" >/dev/null \
    || fail "every service that mounts a secret has group_add [\"$secrets_gid\"]" || return 1
  q '[.services[] | select((.secrets // []) | length > 0) | select(.image // "" | test("(^|/)(postgres|classbeat-db)([:@]|$)"))
      | .user // "" | tostring] | all(. == "postgres" or . == "999" or . == "999:999")' \
    || fail "a postgres service that mounts a secret runs as postgres (gosu would drop the group)" || return 1
  q '[.services[] | select((.secrets // []) | length > 0) | .user // "" | tostring
      | split(":")[] | select(test("^0+$") or . == "root")] | length == 0' \
    || fail "no service that mounts a secret runs as root (uid or gid 0 or root, however written)" || return 1
  # Only the app image has a non-root USER of its own; any other image must name one.
  q '[.services[] | select((.secrets // []) | length > 0) | select(.image // "" | startswith("ghcr.io/magpern/classbeat:") | not)
      | .user // "" | tostring | select(. == "")] | length == 0' \
    || fail "a service that mounts a secret and is not the app image names its non-root user" || return 1
  # With one shared group, any secret file that reaches a container is readable there: no service
  # takes another's mounts (volumes_from) or sees its processes' files (/proc/<pid>/root, through a
  # shared pid or ipc namespace), and the one named volume is a plain local volume (driver_opts could
  # bind secrets/ itself; external, a volume made elsewhere).
  q '[.services[] | select(has("volumes_from"))] | length == 0' \
    || fail "no service uses volumes_from (it would copy another service's secret mounts)" || return 1
  q '[.services[] | .pid // "", .ipc // "" | tostring | select(test("^(host|service:|container:)"))] | length == 0' \
    || fail "no service shares the pid or ipc namespace of the host or another container" || return 1
  q '[.volumes // {} | .[] | keys - ["name"] | length] | all(. == 0)' \
    || fail "the named volume is a plain local volume: its name only (no driver, driver_opts, external or labels)" || return 1
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
  q '[.services[] | select(.privileged == true or ((.devices // []) | length > 0) or ((.cap_add // []) | length > 0)
      or ((.device_cgroup_rules // []) | length > 0) or has("use_api_socket"))] | length == 0' \
    || fail "no service is privileged, gets devices or added capabilities, or the Docker API socket" || return 1
  # A file config is a bind mount of any host path outside Swarm (#512 review B3), and a lifecycle
  # hook runs with its own user and privileges (B4). Production uses neither.
  q '(has("configs") | not) and ([.services[] | select(has("configs"))] | length == 0)' \
    || fail "no configs (a file config is a bind of a host path)" || return 1
  q '[.services[] | select(has("post_start") or has("pre_stop"))] | length == 0' \
    || fail "no post_start or pre_stop hooks (they run with their own user and privileges)" || return 1
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
}

# The compose files as written: no variable named like a client secret, no client secret literal.
check_oauth_files() {
  local d="$1" f
  for f in compose.prod.yaml compose.prod.loopback.yaml compose.prod.lan.yaml; do
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

J="$shared"; check_shared || exit 1
J="$loopback"; check_loopback || exit 1
J="$lan"; check_lan || exit 1
J="$defaults"; check_oauth_defaults || exit 1

if [[ $mode == repo ]]; then   # the self-tests and the checkout-only checks (ADR 0020)
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
  '.services.worker.environment.CLASSBEAT_SOUNDLESS_ENABLED = "true"' \
  '.services.worker.environment.CLASSBEAT_SWEDEBEAT_ENABLED = "true"' \
  '.services.mailpit = {"image":"axllent/mailpit","logging":.services.web.logging}' \
  '.volumes["caddy-data"] = {}' \
  '.services.extra = {"image":"busybox","networks":{"proxy":null},"logging":.services.web.logging}' \
  '.services.extra = {"image":"busybox","networks":{"web-out":null},"logging":.services.web.logging}' \
  '.services.worker.secrets += [{"source":"oauth_google_client_secret","target":"/run/secrets/oauth_google_client_secret"}]' \
  '.services.migrate.secrets += [{"source":"oauth_github_client_secret"}]' \
  '.services.maint = {"image":.services.web.image,"networks":{"backend":null},"logging":.services.web.logging,"secrets":[{"source":"oauth_microsoft_client_secret"}]}' \
  '.services.web.secrets |= map(select(.source != "oauth_github_client_secret"))' \
  '.services.web.secrets |= map(if .source == "oauth_google_client_secret" then .target = "/tmp/google" else . end)' \
  '.secrets.oauth_google_client_secret = {"name":"x","environment":"OAUTH_GOOGLE_CLIENT_SECRET"}' \
  '.secrets.oauth_github_client_secret = {"name":"x","content":"a-literal-secret"}' \
  '.secrets.oauth_microsoft_client_secret.file = "/etc/hostname"' \
  '.services.web.environment.CLASSBEAT_OAUTH_GOOGLE_CLIENT_SECRET = "x"' \
  '.services.worker.environment.OAUTH_GITHUB_CLIENT_SECRET = "x"' \
  '.services.worker.environment.CLASSBEAT_OAUTH_GOOGLE_CLIENT_ID = "check-google-client-id"' \
  '.services.web.environment.CLASSBEAT_OAUTH_GOOGLE_CLIENT_ID = "a-literal-in-compose"' \
  '.services.web.environment.CLASSBEAT_OAUTH_VISIBLE_PROVIDERS = "google"' \
  '.services.web.environment.CLASSBEAT_OAUTH_EXTRA = "x"'; do
  expect_refused shared "$shared" "$m"
done
# Secret files (#511): the group, the users, one name per file, no other way into secrets/.
for m in \
  'del(.services.web.group_add)' \
  'del(.services.migrate.group_add)' \
  '.services.worker.group_add = ["10011"]' \
  '.services.db.group_add = []' \
  'del(.services["db-functions"].group_add)' \
  'del(.services.db.user)' \
  '.services.db.user = "root"' \
  '.services["db-functions"].user = "0"' \
  '.services.migrate.user = "0:0"' \
  '.services.web.user = "root"' \
  '.services.worker.user = "root:10010"' \
  '.secrets.gsec = {"name":"x","file":.secrets.oauth_google_client_secret.file} | .services.worker.secrets += [{"source":"gsec"}]' \
  '.secrets.kek2 = {"name":"x","file":.secrets.connection_kek.file} | .services.migrate.secrets += [{"source":"kek2","target":"/run/secrets/kek2"}]' \
  '.secrets.app_secret_key = {"name":"x","environment":"APP_SECRET_KEY"}' \
  '.secrets.smtp_password.file = "/etc/hostname"' \
  '.services.worker.secrets += [{"source":"oauth_google_client_secret","target":"/run/secrets/app_secret_key"}]' \
  '.services.migrate.secrets |= map(.target = "/run/secrets/database_password")' \
  '.services.worker.volumes = [{"type":"bind","source":(.secrets.app_secret_key.file | rtrimstr("/app_secret_key")),"target":"/s","read_only":true}]' \
  '.services.worker.volumes = [{"type":"bind","source":(.secrets.app_secret_key.file | rtrimstr("/app_secret_key") + "/"),"target":"/s"}]' \
  '.services.web.volumes = [{"type":"bind","source":(.secrets.app_secret_key.file | rtrimstr("/secrets/app_secret_key")),"target":"/x"}]' \
  '.services.web.volumes = [{"type":"bind","source":"/","target":"/host","read_only":true}]' \
  '.services.migrate.volumes = [{"type":"bind","source":.secrets.oauth_github_client_secret.file,"target":"/tmp/g"}]' \
  '.configs = {"c":{"file":.secrets.connection_kek.file}} | .services.web.configs = [{"source":"c","target":"/tmp/c"}]' \
  '.services.web.command = ["sh","-c","cat /run/secrets/oauth_google_*; exec python -m classbeat"]' \
  '.services.worker.entrypoint = ["sh","-c","cat /run/secrets/* >&2"]' \
  '.services.worker.command = "sh -c \"tar c secrets/ | nc x 1\""' \
  '.services.web.healthcheck.test = ["CMD","sh","-c","cat /run/secrets/app_secret_key"]' \
  '.services.db.image = "docker.io/library/postgres:17.11" | del(.services.db.user)' \
  '.services.db.image = "mirror.gcr.io/library/postgres@sha256:00" | .services.db.user = "1000"' \
  '.services.db.image = "example.test/pg:17" | del(.services.db.user)' \
  '.services.worker.volumes_from = ["web"]' \
  '.services.migrate.volumes_from = ["web:ro"]' \
  '.services.extra = {"image":.services.web.image,"networks":{"backend":null},"logging":.services.web.logging,"volumes_from":["container:classbeat-prod-web-1"]}' \
  '.services.worker.pid = "service:web"' \
  '.services.worker.pid = "host"' \
  '.services.migrate.pid = "container:classbeat-prod-web-1"' \
  '.services.worker.ipc = "service:web"' \
  '.services.db.ipc = "host"' \
  '.volumes["db-data"].driver_opts = {"type":"none","o":"bind","device":(.secrets.app_secret_key.file | rtrimstr("/app_secret_key"))}' \
  '.volumes["db-data"].driver_opts = {"type":"none","o":"bind","device":"/"}' \
  '.volumes["db-data"].driver = "local-persist"' \
  '.volumes["db-data"].external = true' \
  '.services.worker.volumes = [{"type":"bind","source":"/proc","target":"/hostproc","read_only":true}]' \
  '.services.worker.volumes = [{"type":"bind","source":"/proc/1/root","target":"/h","read_only":true}]' \
  '.services.web.volumes = [{"type":"bind","source":"/var/run/docker.sock","target":"/var/run/docker.sock"}]' \
  '.services.migrate.volumes = [{"type":"bind","source":"/sys","target":"/s","read_only":true}]' \
  '.services.worker.volumes = [{"type":"bind","source":"/dev/sda1","target":"/d"}]' \
  '.services.worker.volumes = [{"type":"bind","source":(.secrets.app_secret_key.file | rtrimstr("/secrets/app_secret_key") + "/link-to-secrets"),"target":"/s","read_only":true}]' \
  '.services.db.volumes += [{"type":"bind","source":(.secrets.app_secret_key.file | rtrimstr("/secrets/app_secret_key") + "/docker"),"target":"/d","read_only":true}]' \
  '.services["db-functions"].volumes = [{"type":"bind","source":(.secrets.app_secret_key.file | rtrimstr("/secrets/app_secret_key") + "/docker/postgres/queue"),"target":"/queue","read_only":true}]' \
  '.services.db.volumes += [{"type":"bind","source":(.secrets.app_secret_key.file | rtrimstr("/secrets/app_secret_key") + "/docker/postgres/init"),"target":"/docker-entrypoint-initdb.d","read_only":true}]' \
  '.services.db.image = "postgres:17.11" | .services["db-functions"].image = "postgres:17.11"' \
  '.services["db-functions"].image = "ghcr.io/magpern/classbeat-db:9.9.9"' \
  '.services["db-functions"].user = "1000"' \
  '.services.worker.volumes = [{"type":"npipe","source":"//./pipe/docker_engine","target":"/p"}]' \
  '.services.worker.volumes = [{"type":"cluster","source":"x","target":"/c"}]' \
  '.services.worker.privileged = true' \
  '.services.db.devices = [{"source":"/dev/sda","target":"/dev/sda","permissions":"rwm"}]' \
  '.services.db.cap_add = ["DAC_READ_SEARCH"]' \
  '.services.web.cap_add = ["SYS_PTRACE"]' \
  '.services.migrate.device_cgroup_rules = ["b 8:* rmw"]' \
  '.services.worker.use_api_socket = true' \
  '.volumes["db-data"].labels = {"x":"y"}'; do
  expect_refused shared "$shared" "$m"
done
for m in \
  'del(.services.web.group_add)' \
  '.services.worker.volumes_from = ["web"]' \
  '.services.worker.pid = "service:web"' \
  '.services.worker.ipc = "container:classbeat-prod-web-1"' \
  '.services.worker.volumes = [{"type":"bind","source":"/proc","target":"/hostproc","read_only":true}]' \
  '.services.worker.volumes = [{"type":"bind","source":"/proc/1/root","target":"/h","read_only":true}]' \
  '.services.web.volumes = [{"type":"bind","source":"/var/run/docker.sock","target":"/var/run/docker.sock"}]' \
  '.services.worker.privileged = true' \
  '.volumes["db-data"].driver_opts = {"type":"none","o":"bind","device":(.secrets.app_secret_key.file | rtrimstr("/app_secret_key"))}'; do
  expect_refused loopback "$loopback" "$m"
done
expect_refused loopback "$loopback" '.services.worker.volumes = [{"type":"bind","source":(.secrets.app_secret_key.file | rtrimstr("/app_secret_key")),"target":"/s"}]'
# #512 review 3 (B3 to B5) and the numeric root rule, on both variants. The full set of channels is
# follow-up #517 (default-deny key allow-list).
for v in shared loopback lan; do
  case "$v" in shared) base="$shared" ;; loopback) base="$loopback" ;; *) base="$lan" ;; esac
  for m in \
    '.configs = {"c":{"name":"x","file":"/proc"}} | .services.worker.configs = [{"source":"c","target":"/hostproc"}]' \
    '.configs = {"c":{"name":"x","file":"/"}} | .services.worker.configs = [{"source":"c","target":"/host"}]' \
    '.configs = {"c":{"name":"x","file":"/var/run/docker.sock"}} | .services.web.configs = [{"source":"c","target":"/s"}]' \
    '.configs = {"c":{"name":"x","file":(.secrets.app_secret_key.file | rtrimstr("/app_secret_key"))}} | .services.worker.configs = [{"source":"c","target":"/s"}]' \
    '.configs = {"c":{"name":"x","file":"/proc"}}' \
    '.services.worker.configs = [{"source":"c","target":"/s"}]' \
    '.services.web.post_start = [{"command":["sh","-c","id"],"user":"root","privileged":true}]' \
    '.services.worker.pre_stop = [{"command":["sh","-c","id"]}]' \
    '.services.worker.user = "00"' \
    '.services.web.user = "0000:0"' \
    '.services.migrate.user = "10001:0"' \
    '.services.worker.user = "10001:root"'; do
    expect_refused "$v" "$base" "$m"
  done
done
# B5: a service behind `profiles:` must be in the render, so the checks above see it. As YAML merged
# after the production files (and the loopback overlay), with every profile like the renders above.
mkdir "$tmp/yaml"
ln -s ../secrets "$tmp/yaml/secrets"
cp "$tmp/compose.prod.yaml" "$tmp/compose.prod.loopback.yaml" "$tmp/yaml/"
printf '%s\n' 'services:' '  spy:' '    image: busybox' '    profiles: [ops]' '    pid: host' '    privileged: true' \
  '    group_add: ["10010"]' '    volumes:' '      - /:/host' > "$tmp/yaml/profiled.yaml"
ydir="$native/${tmp##*/}/yaml"
spy_s="$(render -f "$ydir/compose.prod.yaml" -f "$ydir/profiled.yaml")"
spy_l="$(render -f "$ydir/compose.prod.yaml" -f "$ydir/compose.prod.loopback.yaml" -f "$ydir/profiled.yaml")"
if ! jq -e '.services | has("spy")' <<<"$spy_s" >/dev/null || ! jq -e '.services | has("spy")' <<<"$spy_l" >/dev/null; then
  echo "check-prod-compose: self-test: the render left out a profiled service" >&2; exit 1
fi
if (J="$spy_s"; check_shared 2>/dev/null) || (J="$spy_l"; check_loopback 2>/dev/null); then
  echo "check-prod-compose: self-test: accepted a profiled service with pid host, privileged and a bind of /" >&2; exit 1
fi
# The client secret's value (the canary in the dummy file) anywhere in the rendered file.
expect_refused shared "$shared" ".services.web.labels = {\"note\": \"$oauth_canary-google\"}"
for m in \
  '.services.web.environment.CLASSBEAT_OAUTH_GOOGLE_CLIENT_ID = "a-literal-in-compose"' \
  '.services.web.environment.CLASSBEAT_OAUTH_VISIBLE_PROVIDERS = "google,microsoft"' \
  'del(.services.web.environment.CLASSBEAT_OAUTH_MICROSOFT_CLIENT_ID)'; do
  expect_refused oauth_defaults "$defaults" "$m"
done
# The compose files as written (copies; the checkout's files are never changed).
mkdir "$tmp/files"
expect_files_refused() {
  cp "$tmp/compose.prod.yaml" "$tmp/compose.prod.loopback.yaml" "$tmp/files/"
  sed -i -E "$2" "$tmp/files/$1"
  if (check_oauth_files "$tmp/files" 2>/dev/null); then
    echo "check-prod-compose: self-test: accepted an unsafe $1: $2" >&2; exit 1
  fi
}
# shellcheck disable=SC2016  # a literal compose variable, not a shell one
expect_files_refused compose.prod.yaml \
  's|^(      CLASSBEAT_OAUTH_VISIBLE_PROVIDERS:.*)$|\1\n      CLASSBEAT_OAUTH_GOOGLE_CLIENT_SECRET: ${OAUTH_GOOGLE_CLIENT_SECRET:-}|'
expect_files_refused compose.prod.yaml 's|^  oauth_google_client_secret:$|  oauth_google_client_secret: {content: abc}|'
expect_files_refused compose.prod.yaml 's|file: \./secrets/oauth_github_client_secret|environment: OAUTH_GITHUB_CLIENT_SECRET|'
expect_files_refused compose.prod.loopback.yaml \
  's|^(      CLASSBEAT_TRUSTED_PROXIES:.*)$|\1\n      OAUTH_CLIENT_SECRET: abc|'
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
  '.services.worker.ports = [{"host_ip":"127.0.0.1","target":8000,"published":"8081"}]' \
  '.services.worker.secrets += [{"source":"oauth_microsoft_client_secret"}]'; do
  expect_refused loopback "$loopback" "$m"
done

# The LAN overlay (#567): one port on the one private address, web off the proxy network.
for m in \
  '.services.web.ports[0].host_ip = "0.0.0.0"' \
  '.services.web.ports[0].host_ip = "::"' \
  '.services.web.ports[0].host_ip = "127.0.0.1"' \
  '.services.web.ports[0].host_ip = "192.168.1.21"' \
  'del(.services.web.ports[0].host_ip)' \
  '.services.web.ports[0].target = 5432' \
  '.services.web.ports[0].published = "80"' \
  '.services.web.ports[0].protocol = "udp"' \
  '.services.web.ports += [{"host_ip":"192.168.1.20","target":8000,"published":"8081"}]' \
  '.services.web.networks.proxy = {"aliases":["classbeat-web"]}' \
  '.services.web.environment.CLASSBEAT_TRUSTED_PROXIES = "172.29.80.1"' \
  '.services.web.environment.CLASSBEAT_TRUSTED_PROXIES = "192.168.1.20"' \
  '.services.web.environment.CLASSBEAT_TRUSTED_PROXIES = "192.168.1.0/24"' \
  '.services.worker.ports = [{"host_ip":"192.168.1.20","target":8000,"published":"8082"}]' \
  '.services.db.ports = [{"host_ip":"192.168.1.20","target":5432,"published":"5432"}]'; do
  expect_refused lan "$lan" "$m"
done
fi

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

# Where the real stack publishes web (install mode): only web publishes a port, and only on loopback
# (the host's own proxy) or on one private LAN address (compose.prod.lan.yaml, swag on another host),
# never on every interface; swag is not this server (#567).
check_real_ports() {   # check_real_ports RENDERED_JSON
  local j="$1" host trusted
  jq -e '[.services | to_entries[] | select(.key != "web") | select(.value.ports // [] | length > 0)] | length == 0' <<<"$j" >/dev/null \
    || fail "only web may publish a port" || return 1
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
# --- The real secret files on the server (#511) ---------------------------------------------------
# Only the metadata of each file compose.prod.yaml names (stat: type, group number, mode); a file is
# never opened, and nothing but its name, mode and group number is printed. Each must be a regular
# file (not a link), group secrets_gid, readable by the group, not writable by it, nothing for
# others; the folder gives others nothing. Runs on the server: when the env file's COMPOSE_FILE names
# compose.prod.yaml (runbook section 5), or when PROD_CHECK_SECRETS_DIR names a folder to check.
# Every failure says what to run; the steps that need sudo are marked (an owner outside the group
# cannot set the group or the setgid bit on the folder, which is why the first install needs sudo
# once, runbook section 5).
check_secret_files() {   # check_secret_files DIR GID NAME...
  local d="$1" gid="$2" n st g a m ds
  shift 2
  [[ -d "$d" && ! -L "$d" ]] || fail "$d is not a folder (runbook section 5: mkdir secrets, then the one-time sudo step)" || return 1
  ds="$(stat -c '%g %a' "$d")" || fail "$d: stat failed" || return 1
  g="${ds% *}"; a="${ds#* }"
  (( (8#$a & 8#007) == 0 )) || fail "$d: mode $a gives others access: chmod 2700 $d" || return 1
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
(( ${#secret_names[@]} >= 14 )) || { fail "compose.prod.yaml names fewer secrets than expected"; exit 1; }
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
  'rm "$st_dir/oauth_google_client_secret"' 'chmod 755 "$st_dir"' 'chmod 2701 "$st_dir"' \
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
if [[ $mode == repo ]]; then   # the self-tests and the checkout-only checks (ADR 0020)
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

fi
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
  mapfile -t svcs < <(docker compose config --format json | jq -r '.services | to_entries[]
    | select((.value.secrets // []) | length > 0) | .key')
  (( ${#svcs[@]} > 0 )) || { fail "no service mounts a secret (wrong COMPOSE_FILE?)"; return 1; }
  for svc in "${svcs[@]}"; do
    out="$(docker compose run --rm --no-deps -T --entrypoint sh "$svc" -c \
      'for f in /run/secrets/*; do if test -r "$f"; then echo "${f##*/} OK"; else echo "${f##*/} FAIL"; fi; done' \
      2>"$tmp/probe-$svc.log")" \
      || { cat "$tmp/probe-$svc.log" >&2; fail "$svc: could not run the pulled image (docker compose pull first?)"; return 1; }
    probe_verdict "$svc" "$out" || rc=1
  done
  return "$rc"
}
if (( probe )); then
  ( restore_real_env; probe_secrets ) || exit 1
  owner_note="$owner_note; every service reads every secret it mounts"
fi

if [[ $mode == install ]]; then
  echo "install check (compose files with dummies, .env, secrets/): ok; $owner_note"
  exit 0
fi
echo "compose.prod.yaml (shared network and loopback, OAuth secrets on web only, secret files by group 10010), runbook network, swag site config and self-tests: ok; $owner_note"
