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
#    user, each secret is secrets/<its own name>; #517: DEFAULT DENY, every key of the rendered files
#    (all profiles) must be on the allow-list of check_keys, and env_file, label_file, profiles,
#    extends and include are refused in the files' text (a lint); #610: each service's variables by
#    name (whatever an env_file, however spelled, adds is refused, its name never printed) and the
#    top-level networks by key), and compose.prod.yaml's defaults leave every OAuth client id empty;
# (the SQL the database services run is in the release's classbeat-db image: no bind mounts, ADR 0020)
# 3. a self-test: each unsafe mutation of the rendered files must fail step 2 (fails closed);
# 4. the owner's real PROXY_IP, when one is set (their shell, else PROD_CHECK_ENV_FILE, default the
#    checkout's .env): compose.prod.yaml rendered with it must trust one private IPv4 address (#495);
# 4b. that env file (and, in a checkout, .env.example) holds no *CLIENT_SECRET* variable; and in the compose
#    files (the three production ones, in a checkout also the dev compose.yaml) `client_secret` appears
#    only as the three secret names and their ./secrets/ files, in check_oauth_files (step 2): a client
#    secret is a file under secrets/ only;
# 4c. on the server (that env file's COMPOSE_FILE names compose.prod.yaml, or PROD_CHECK_SECRETS_DIR
#    is set): each real secret file is a regular file of group 10010, mode 640 or stricter, in a
#    folder that gives the group and others nothing (mode 2700), and no host account has that group
#    (#511, #517; metadata only, no content);
# 4d. in an install folder, when the swag container is running (PROD_CHECK_SWAG_CONTAINER, default
#    `swag`) and web trusts PROXY_IP itself: PROXY_IP is one of that container's addresses (#500;
#    a typo inside Docker's range passes 4); not visible (no Docker daemon, no such container) is
#    a note, not a failure;
# 5. the runbook's `docker network create` keeps swag's fixed address outside Docker's --ip-range;
# 6. `nginx -t` accepts docs/runbooks/swag-classbeat.subdomain.conf, and the directives the app's
#    client address, body cap, SSE stream and headers rely on are in it. The file is read as nginx
#    reads it (directives and blocks, not lines, #500): X-Forwarded-For is set exactly once, in
#    the server, to $remote_addr, and no location sets a proxy_set_header or add_header;
# 6b. PROD_CHECK_SWAG_CONF=/path/to/the/real/file (runbook sections 7 and 9): the same rules and
#    `nginx -t` on the owner's live swag file, in either mode (with DNS only, the Cloudflare include
#    and real_ip_header may both be absent);
# and a self-test for 4, 4b, 4c, 4d, 5 and 6 as for 2. On the server: run it in /opt/classbeat after writing
# .env (runbook section 9).
#
# What it is: a best-effort lint of the owner's own committed, reviewed compose files against honest
# mistakes. It is NOT a security boundary against someone who can edit those files (they could run
# anything). The boundary for the secrets is on the server: the files' mode and group (4c, runbook
# section 5) and which services mount which secret. Its compose rules are default-deny (#517): a key
# or a service the allow-list (check_keys) does not name is refused, so adding one to the production
# files needs a reviewed change to this script (runbook section 13). The swag rules read directives, but are the
# same kind of lint: they refuse the known ways to forge the client address, and nginx -t is the
# syntax check; the file is also read by swag, not by this script.
# Two places it runs (ADR 0020, #566):
#   a checkout (CI and developers): everything above, including the self-tests, the runbook and
#     the swag sample (needs docs/ and the nginx image);
#   an install folder (the released script next to compose.prod.yaml, no docs/): the pre-deploy
#     check on the server, `./check-prod-compose.sh [--probe]` from /opt/classbeat. It checks the
#     files there with dummy values (so a local edit of a compose file is caught), renders the real
#     .env, runs that real render (COMPOSE_FILE, every profile) through the same allow-list and
#     secret-mount rules (#610; the documented compose.bridge.yaml shape is allowed), checks the real
#     PROXY_IP, the env file and the real secret files and their folder, and
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
# The owner's own swag site file (runbook sections 7 and 9), checked with the same rules as the sample
# (#500). Made absolute here, before the script changes directory.
swag_live="${PROD_CHECK_SWAG_CONF:-}"
if [[ -n "$swag_live" ]]; then
  [[ "$swag_live" == /* || "$swag_live" =~ ^[A-Za-z]:[/\\] ]] || swag_live="$PWD/$swag_live"
  [[ -f "$swag_live" && -r "$swag_live" ]] \
    || { echo "check-prod-compose: PROD_CHECK_SWAG_CONF=$swag_live is not a readable file (copy it somewhere you can read, runbook section 9)" >&2; exit 1; }
fi
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# A checkout has scripts/release-assets.sh next to this script; the released install folder never does
# (it is not an asset). The test needs it, or a parent folder that happens to hold a compose.prod.yaml
# and docs/runbooks would make an install folder inside it look like a checkout (#470, review of #575).
if [[ -f "$here/release-assets.sh" && -f "$here/../compose.prod.yaml" && -d "$here/../docs/runbooks" ]]; then
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
  files="$(docker run --rm --network none --entrypoint ls "ghcr.io/magpern/classbeat:$old" /app/migrations/versions 2>/dev/null)" \
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
  # Compose's own error output is never printed, on this or any render (#616 review): it quotes the
  # offending line of an env_file, so a secret file that does not parse as one (a `~` in a client
  # secret, a JSON key, base64) would be printed. A fixed sentence instead; the owner can run
  # `docker compose config -q` by hand to see Compose's message on their own terminal.
  real_json="$(docker compose config --format json 2>/dev/null)" \
    || { echo "check-prod-compose: docker compose config fails with your .env: a required variable is unset (CLASSBEAT_VERSION, CLASSBEAT_DOMAIN, PROXY_IP, SMTP_HOST, SMTP_SENDER) or a file in COMPOSE_FILE, or an env_file it names, does not parse. Compose's message is not printed (it can quote a line of a secret file): run \`docker compose config -q\` yourself to read it" >&2; exit 1; }
  # The same with every profile, for the allow-list (check_real, #610).
  real_all_json="$(docker compose --profile '*' config --format json 2>/dev/null)" \
    || { echo "check-prod-compose: docker compose --profile '*' config fails with your .env although the plain render passes: a service behind a profile needs a variable or file the others do not (run \`docker compose --profile '*' config -q\` yourself to read Compose's message; it is not printed here)" >&2; exit 1; }
fi

# Copies next to dummy secret files, so the checkout's own secrets/ is never read.
cp compose.prod.yaml compose.prod.loopback.yaml compose.prod.lan.yaml "$tmp/"
mkdir "$tmp/secrets"
oauth_providers=(google github microsoft)
for n in postgres_password database_owner_password database_password database_maint_password \
  app_secret_key connection_kek smtp_password anthropic_api_key openai_api_key deepseek_api_key swedebeat_app; do
  # One line without `=` (a dummy, never a real secret): an env_file of it, however spelled, gives
  # the variable "" in the render, which check_keys refuses (#610); an empty file would add nothing.
  printf 'dummy-secret-%s' "$n" > "$tmp/secrets/$n"
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
# Compose's error output is never printed (#616 review: it can quote a line of a secret file).
render() {
  docker compose "$@" --profile '*' config --format json 2>/dev/null \
    || { echo "check-prod-compose: docker compose config fails on the compose files with dummy values (Compose's message is not printed: run \`docker compose config -q\` yourself)" >&2; return 1; }
}
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
  check_keys || return 1
  check_oauth || return 1
  check_secret_mounts || return 1
}

# Default deny (#517, after the #512 reviews B1 to B5 found one channel after another). Every key of
# the rendered file (rendered with every profile) must be on this allow-list, which is exactly what
# compose.prod.yaml and its two overlays use today: at the top level, per service BY NAME (a new
# service is refused) and in each nested block. Anything else is refused by its name: configs,
# post_start and pre_stop, profiles, pid, ipc, privileged, cap_add, devices, volumes_from, userns_mode,
# cgroup, cgroup_parent, uts, runtime, gpus, provider, env_file, a bind or a driver option on a
# volume, a key Compose adds later. Adding a key or a service to the production files needs a change
# here, reviewed with it (runbook section 13). The value checks below and in check_secret_mounts still
# apply to the allowed keys. The render drops env_file but copies its variables into `environment`,
# which is allowed by variable name per service (#610), and keeps label_file and profiles (refused here);
# include and extends are resolved into it. check_prod_file_text is a fast lint of the files' text for the
# same keys, not the check: a quoted or explicit (`? env_file`) key gets past a line match. Top-level
# networks are allowed by key too (#598 review finding 5).
# shellcheck disable=SC2016  # jq variables, not shell ones
check_keys() {
  local bad
  bad="$(jq -r '
    def allowed_services: {
      "db": ["command","entrypoint","environment","group_add","healthcheck","image","logging","networks",
             "restart","secrets","user","volumes"],
      "db-functions": ["command","depends_on","entrypoint","group_add","image","logging","networks","restart",
             "secrets","tmpfs","user"],
      "migrate": ["cap_drop","command","depends_on","entrypoint","environment","group_add","image","logging",
             "networks","restart","secrets","security_opt"],
      "web": ["cap_drop","command","depends_on","entrypoint","environment","expose","group_add","healthcheck",
             "image","logging","networks","ports","read_only","restart","secrets","security_opt","tmpfs"],
      "worker": ["cap_drop","command","depends_on","entrypoint","environment","group_add","image","logging",
             "networks","restart","secrets","security_opt","stop_grace_period"]
    };
    # The keys of $o that are not in $allowed, named with their path; a non-mapping is refused whole.
    def extra($o; $allowed; $where):
      if $o == null then empty
      elif ($o | type) != "object" then "\($where) (not a mapping)"
      else ($o | keys - $allowed)[] | "\($where).\(.)" end;
    ((keys | map(select(startswith("x-") | not))) - ["name","services","networks","volumes","secrets"])[],
    (.services | to_entries[] | .key as $n | .value as $v
      | if (allowed_services | has($n)) | not then "services.\($n)"
        else
          extra($v; allowed_services[$n]; "services.\($n)"),
          ($v.secrets // [] | .[] | extra(.; ["source","target"]; "services.\($n).secrets[]")),
          ($v.volumes // [] | .[] | extra(.; ["type","source","target","volume"]; "services.\($n).volumes[]"),
             extra(.volume; []; "services.\($n).volumes[].volume")),
          extra($v.healthcheck; ["test","interval","timeout","retries","start_period"]; "services.\($n).healthcheck"),
          extra($v.logging; ["driver","options"]; "services.\($n).logging"),
          extra($v.logging.options; ["max-size","max-file"]; "services.\($n).logging.options"),
          ($v.depends_on // {} | .[] | extra(.; ["condition","required"]; "services.\($n).depends_on.*")),
          ($v.networks // {} | .[] | extra(.; ["aliases"]; "services.\($n).networks.*")),
          ($v.ports // [] | .[] | extra(.; ["host_ip","mode","protocol","published","target"]; "services.\($n).ports[]"))
        end),
    (.networks // {} | to_entries[] | .key as $n
      | extra(.value; ["name","external","internal","ipam"]; "networks.\($n)"),
        extra(.value.ipam; ["config"]; "networks.\($n).ipam"),
        (.value.ipam.config // [] | .[] | extra(.; ["subnet","gateway"]; "networks.\($n).ipam.config[]"))),
    (.volumes // {} | to_entries[] | extra(.value; ["name"]; "volumes.\(.key)")),
    (.secrets // {} | to_entries[] | extra(.value; ["name","file"]; "secrets.\(.key)"))
  ' <<<"$J")" || fail "the rendered file could not be read as compose keys" || return 1
  [[ -z "$bad" ]] || fail "not on the allow-list of compose keys (check_keys in scripts/check-prod-compose.sh): $(tr '\n' ' ' <<<"$bad")" \
    || return 1
  # Each service's variables, by name (#610). The render drops env_file but copies the file into
  # `environment`, however the key was spelled (`"env_file"`, `? env_file`, a flow mapping), so the
  # text rule (check_prod_file_text) is only a fast lint and this is the check: a variable the files do
  # not set is refused. Its name is never printed: an env_file of a secret file gives a variable whose
  # name or value is the secret (a line without `=` becomes the name "" with the line as its value).
  bad="$(jq -r '
    def app: ["CLASSBEAT_ADMIN_EMAILS","CLASSBEAT_CONNECTION_KEK_ID","CLASSBEAT_DATABASE_URL",
      "CLASSBEAT_DEEPSEEK_ENABLED","CLASSBEAT_ENVIRONMENT","CLASSBEAT_LOG_LEVEL","CLASSBEAT_MAIL_SUPPORT_ADDRESS","CLASSBEAT_PUBLIC_BASE_URL",
      "CLASSBEAT_SIGNUP_MODE","CLASSBEAT_SMTP_HOST","CLASSBEAT_SMTP_PORT","CLASSBEAT_SMTP_SENDER",
      "CLASSBEAT_SMTP_TLS","CLASSBEAT_SMTP_USERNAME"];
    def flags: ["CLASSBEAT_RUNNING_ENABLED","CLASSBEAT_SOUNDLESS_ENABLED","CLASSBEAT_SWEDEBEAT_ENABLED"];
    def allowed_env: {
      "db": ["POSTGRES_DB","POSTGRES_PASSWORD_FILE"],
      "db-functions": [],
      "migrate": (app + ["CLASSBEAT_MIGRATIONS_DATABASE_URL"]),
      "worker": (app + flags + ["CLASSBEAT_MODEL_PROVIDER"]),
      "web": (app + flags + ["CLASSBEAT_MAINT_DATABASE_URL","CLASSBEAT_OAUTH_GITHUB_CLIENT_ID",
        "CLASSBEAT_OAUTH_GOOGLE_CLIENT_ID","CLASSBEAT_OAUTH_MICROSOFT_CLIENT_ID","CLASSBEAT_OAUTH_VISIBLE_PROVIDERS",
        "CLASSBEAT_SMTP_CA_FILE","CLASSBEAT_TRUSTED_PROXIES"])
    };
    .services | to_entries[] | .key as $n | (.value.environment // {}) as $e
    | if ($e | type) != "object" then "services.\($n).environment (not a mapping)"
      else (($e | keys) - (allowed_env[$n] // [])) as $x
        | ((allowed_env[$n] // []) - ($e | keys)) as $m
        | (if ($x | length) > 0 then "services.\($n).environment: \($x | length) variable(s) the production files do not set (names not printed)" else empty end),
          (if ($m | length) > 0 then "services.\($n).environment lacks \($m | join(" "))" else empty end)
      end' <<<"$J")" || fail "the rendered environment could not be read" || return 1
  [[ -z "$bad" ]] || fail "variables not on the allow-list (an env_file, however spelled, is copied into environment; check_keys): $(tr '\n' ' ' <<<"$bad")" \
    || return 1
  # Top-level networks by key (#598 review, finding 5): only the ones the shipped files use. proxy is
  # the owner's external network; every other one is the project's own (not external, its default
  # name `<project>_<key>`), so a `name:` cannot join web or the worker to some other existing network.
  bad="$(jq -r '.name as $p | .networks // {} | to_entries[]
      | if (.key | IN("backend","egress","proxy","web-out")) | not then "networks.\(.key)"
        elif .key != "proxy" and ((.value.external // false) or .value.name != ($p + "_" + .key))
        then "networks.\(.key) (external or renamed)" else empty end' <<<"$J")" \
    || fail "the rendered networks could not be read" || return 1
  [[ -z "$bad" ]] || fail "networks: only backend, egress, web-out (the project's own) and the external proxy: $(tr '\n' ' ' <<<"$bad")" \
    || return 1
  # The values of the allowed keys that reach the host or another container.
  q '[.services[] | .logging.driver] | all(. == "json-file")' || fail "every service logs with json-file" || return 1
  q '[.services[] | select(has("security_opt")) | .security_opt] | all(. == ["no-new-privileges:true"])' \
    || fail "security_opt is exactly [\"no-new-privileges:true\"] (no seccomp, apparmor, systempaths or label option)" || return 1
  q '[.services[] | select(has("cap_drop")) | .cap_drop] | all(. == ["ALL"])' \
    || fail "cap_drop is exactly [\"ALL\"]" || return 1
  jq -e --arg g "$secrets_gid" '[.services[] | select(has("group_add")) | .group_add | map(tostring)] | all(. == [$g])' <<<"$J" >/dev/null \
    || fail "group_add is exactly [\"$secrets_gid\"] (no other group, root's included)" || return 1
  # The command, entrypoint and tmpfs mounts are the ones the files have: a changed one is a reviewed
  # change to this list (an entrypoint or a tmpfs option can reach what the keys above cannot).
  q 'def known: {
       "db": {}, "web": {"tmpfs": ["/tmp"]}, "worker": {"command": ["python","-m","classbeat.worker"]},
       "migrate": {"command": ["alembic","upgrade","head"]},
       "db-functions": {"entrypoint": ["sh","/queue/db-functions.sh"], "tmpfs": ["/run/pgpass:mode=0700,uid=999,gid=999"]}
     };
     [.services | to_entries[] | .key as $n | .value as $v | known[$n] as $k
       | ["command","entrypoint","tmpfs"][] | select(($v[.] // null) != ($k[.] // null))] | length == 0' \
    || fail "command, entrypoint and tmpfs are the ones in the production files" || return 1
}

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

if [[ $mode == repo ]]; then   # the self-tests and the checkout-only checks (ADR 0020)
# Self-test: every mutation is unsafe and must be refused.
expect_refused() {   # expect_refused VARIANT BASE MUTATION [FRAGMENT of the message it must give]
  local variant="$1" base="$2" mutation="$3" why="${4:-}"
  J="$(jq "$mutation" <<<"$base")" \
    || { echo "check-prod-compose: self-test: a mutation does not apply: $mutation" >&2; exit 1; }
  if ("check_$variant" 2>"$tmp/refuse.log"); then
    echo "check-prod-compose: self-test: accepted an unsafe $variant file: $mutation" >&2
    exit 1
  fi
  if [[ -n "$why" ]] && ! grep -qF -- "$why" "$tmp/refuse.log"; then
    cat "$tmp/refuse.log" >&2
    echo "check-prod-compose: self-test: refused for another reason than '$why' ($variant): $mutation" >&2
    exit 1
  fi
}
expect_accepted() {   # expect_accepted VARIANT BASE MUTATION: a harmless change must still pass
  local variant="$1" base="$2" mutation="$3"
  J="$(jq "$mutation" <<<"$base")" \
    || { echo "check-prod-compose: self-test: a mutation does not apply: $mutation" >&2; exit 1; }
  ("check_$variant" 2>"$tmp/accept.log") \
    || { cat "$tmp/accept.log" >&2; echo "check-prod-compose: self-test: refused a harmless $variant change: $mutation" >&2; exit 1; }
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
# Default deny, on EVERY variant (#517; the #512 review's B1 to B5, the numeric-root rule, and keys and
# services nobody has heard of). Each entry is FRAGMENT|MUTATION: the mutated render must be refused
# and the message must contain FRAGMENT, so a mutation that trips some other rule, or does not apply,
# cannot pass for the rule it is for.
sd='(.secrets.app_secret_key.file | rtrimstr("/app_secret_key"))'
deny=(
  # B1: another service's mounts, a volume that is a bind of any host path, shared namespaces
  'services.worker.volumes_from|.services.worker.volumes_from = ["web"]'
  'services.migrate.volumes_from|.services.migrate.volumes_from = ["web:ro"]'
  'volumes.db-data.driver_opts|.volumes["db-data"].driver_opts = {"type":"none","o":"bind","device":'"$sd"'}'
  'volumes.db-data.driver_opts|.volumes["db-data"].driver_opts = {"type":"none","o":"bind","device":"/"}'
  'volumes.db-data.driver|.volumes["db-data"].driver = "local-persist"'
  'volumes.db-data.external|.volumes["db-data"].external = true'
  'services.worker.pid|.services.worker.pid = "service:web"'
  'services.worker.pid|.services.worker.pid = "host"'
  'services.migrate.pid|.services.migrate.pid = "container:classbeat-prod-web-1"'
  'services.worker.ipc|.services.worker.ipc = "service:web"'
  'services.db.ipc|.services.db.ipc = "host"'
  # B2: binds of /proc, /proc/1/root, the Docker socket and the secrets folder itself
  'services.worker.volumes|.services.worker.volumes = [{"type":"bind","source":"/proc","target":"/hostproc","read_only":true}]'
  'services.worker.volumes|.services.worker.volumes = [{"type":"bind","source":"/proc/1/root","target":"/h","read_only":true}]'
  'services.web.volumes|.services.web.volumes = [{"type":"bind","source":"/var/run/docker.sock","target":"/var/run/docker.sock"}]'
  'services.migrate.volumes|.services.migrate.volumes = [{"type":"bind","source":'"$sd"',"target":"/s"}]'
  'no bind mounts|.services.db.volumes += [{"type":"bind","source":"/proc","target":"/hostproc"}]'
  'no bind mounts|.services.db.volumes += [{"type":"bind","source":"/proc/1/root","target":"/h"}]'
  'no bind mounts|.services.db.volumes += [{"type":"bind","source":"/var/run/docker.sock","target":"/var/run/docker.sock"}]'
  'no bind mounts|.services.db.volumes += [{"type":"npipe","source":"//./pipe/docker_engine","target":"/p"}]'
  'bind|.services.db.volumes += [{"type":"bind","source":'"$sd"',"target":"/s"}]'
  # B3: configs (a file config is a bind of any host path)
  'configs|.configs = {"c":{"name":"x","file":"/proc"}} | .services.worker.configs = [{"source":"c","target":"/hostproc"}]'
  'configs|.configs = {"c":{"name":"x","file":"/"}} | .services.worker.configs = [{"source":"c","target":"/host"}]'
  'configs|.configs = {"c":{"name":"x","file":"/var/run/docker.sock"}} | .services.web.configs = [{"source":"c","target":"/s"}]'
  'configs|.configs = {"c":{"name":"x","file":'"$sd"'}} | .services.worker.configs = [{"source":"c","target":"/s"}]'
  'configs|.configs = {"c":{"name":"x","file":"/proc"}}'
  'services.worker.configs|.services.worker.configs = [{"source":"c","target":"/s"}]'
  # B4: lifecycle hooks with their own user and privileges
  'services.web.post_start|.services.web.post_start = [{"command":["sh","-c","id"],"user":"root","privileged":true}]'
  'services.worker.pre_stop|.services.worker.pre_stop = [{"command":["sh","-c","id"]}]'
  # B5: a service behind profiles (the render holds it because of --profile "*"): a new service
  'services.spy|.services.spy = {"image":"busybox","profiles":["ops"],"pid":"host","privileged":true,"group_add":["10010"],"volumes":[{"type":"bind","source":"/","target":"/host"}],"logging":.services.web.logging}'
  'services.web.profiles|.services.web.profiles = ["ops"]'
  # root, however written (a numeric zero, a sign, a group, the name)
  'services.worker.user|.services.worker.user = "00"'
  'services.web.user|.services.web.user = "0000:0"'
  'services.migrate.user|.services.migrate.user = "10001:0"'
  'services.worker.user|.services.worker.user = "10001:root"'
  'services.web.user|.services.web.user = "10001"'
  'runs as root|.services.db.user = "+0"'
  'runs as root|.services.db.user = "0"'
  'runs as root|.services.db.user = "0:0"'
  'runs as root|.services["db-functions"].user = "0000:0"'
  'runs as root|.services.db.user = "postgres:0"'
  'runs as root|.services["db-functions"].user = "999:root"'
  'runs as root|.services.db.user = "root"'
  'runs as root|.services.db.user = "00"'
  'runs as postgres|.services.db.user = "1000"'
  'runs as postgres|del(.services["db-functions"].user)'
  'runs as root|.services.db.user = 0'
  # a brand-new key, a brand-new top-level key and a brand-new service
  'services.web.brand_new_compose_key|.services.web.brand_new_compose_key = true'
  'services.db.brand_new_compose_key|.services.db.brand_new_compose_key = {"a":1}'
  'brand_new_top_level|.brand_new_top_level = {}'
  'services.brand_new_service|.services.brand_new_service = {"image":.services.web.image,"logging":.services.web.logging}'
  # host-reaching keys the issue names
  'services.worker.userns_mode|.services.worker.userns_mode = "host"'
  'services.worker.cgroup|.services.worker.cgroup = "host"'
  'services.worker.cgroup_parent|.services.worker.cgroup_parent = "/"'
  'services.worker.uts|.services.worker.uts = "host"'
  'services.worker.runtime|.services.worker.runtime = "runc"'
  'services.worker.gpus|.services.worker.gpus = "all"'
  'services.worker.provider|.services.worker.provider = {"type":"x"}'
  'services.worker.env_file|.services.worker.env_file = [{"path":"./secrets/app_secret_key"}]'
  'services.worker.label_file|.services.worker.label_file = ["./secrets/app_secret_key"]'
  'services.worker.privileged|.services.worker.privileged = true'
  'services.db.devices|.services.db.devices = [{"source":"/dev/sda","target":"/dev/sda","permissions":"rwm"}]'
  'services.db.cap_add|.services.db.cap_add = ["DAC_READ_SEARCH"]'
  'services.migrate.device_cgroup_rules|.services.migrate.device_cgroup_rules = ["b 8:* rmw"]'
  'services.worker.use_api_socket|.services.worker.use_api_socket = true'
  'services.web.network_mode|.services.web.network_mode = "host"'
  # security_opt exactly no-new-privileges, cap_drop exactly ALL, group_add exactly 10010
  'security_opt is exactly|.services.worker.security_opt += ["seccomp=unconfined"]'
  'security_opt is exactly|.services.web.security_opt = ["no-new-privileges:true","apparmor=unconfined"]'
  'security_opt is exactly|.services.migrate.security_opt = ["no-new-privileges:true","systempaths=unconfined"]'
  'security_opt is exactly|.services.worker.security_opt = ["no-new-privileges:true","label=disable"]'
  'cap_drop is exactly|.services.migrate.cap_drop = ["NET_RAW"]'
  'cap_drop is exactly|.services.migrate.cap_drop = ["ALL","CHOWN"]'
  'group_add is exactly|.services.worker.group_add = ["10010","0"]'
  'group_add is exactly|.services.db.group_add = ["0"]'
  # nested blocks
  'services.worker.secrets[].mode|.services.worker.secrets[0].mode = 292'
  'services.worker.secrets[].uid|.services.worker.secrets[0].uid = "0"'
  'services.db.volumes[].bind|.services.db.volumes[0].bind = {"create_host_path":true}'
  'services.db.volumes[].volume.subpath|.services.db.volumes[0].volume.subpath = "x"'
  'services.web.healthcheck.start_interval|.services.web.healthcheck.start_interval = "1s"'
  'services.web.logging.options.tag|.services.web.logging.options.tag = "x"'
  'every service logs with json-file|.services.web.logging.driver = "syslog"'
  'services.web.ports[].x_extra|.services.web.ports = [{"host_ip":"127.0.0.1","target":8000,"published":"8080","x_extra":1}]'
  'services.worker.depends_on.*.restart|.services.worker.depends_on["db-functions"].restart = true'
  'services.worker.networks.*.ipv4_address|.services.worker.networks.egress = {"ipv4_address":"10.0.0.9"}'
  'networks.egress.driver_opts|.networks.egress.driver_opts = {"x":"y"}'
  'networks.backend.ipam.driver|.networks.backend.ipam.driver = "x"'
  'networks.egress.attachable|.networks.egress.attachable = true'
  'secrets.app_secret_key.x_extra|.secrets.app_secret_key.x_extra = 1'
  # a changed command, entrypoint or tmpfs
  'command, entrypoint and tmpfs|.services.web.entrypoint = ["sh","-c","id"]'
  'command, entrypoint and tmpfs|.services.db.command = ["postgres","-c","x=y"]'
  'command, entrypoint and tmpfs|.services["db-functions"].entrypoint = ["sh","-c","id"]'
  'command, entrypoint and tmpfs|.services.web.tmpfs = ["/tmp:exec"]'
  'services.worker.tmpfs|.services.worker.tmpfs = ["/x"]'
  # variables by name (#610): what an env_file, however spelled, leaves in the render
  'services.worker.environment: 1 variable|.services.worker.environment[""] = "a-secret-file-line"'
  'services.web.environment: 1 variable|.services.web.environment.SPY = "1"'
  'services.db.environment: 1 variable|.services.db.environment.CLASSBEAT_DATABASE_URL = "x"'
  'services.worker.environment: 2 variable|.services.worker.environment += {"CLASSBEAT_MAINT_DATABASE_URL":"x","A":"b"}'
  'services.migrate.environment lacks CLASSBEAT_MIGRATIONS_DATABASE_URL|del(.services.migrate.environment.CLASSBEAT_MIGRATIONS_DATABASE_URL)'
  # top-level networks by key, and the project's own by name (#598 review, finding 5)
  'networks.spy|.networks.spy = {"name":"classbeat-prod-check_spy","ipam":{}}'
  'networks.backendx|.networks.backendx = {"name":"classbeat-prod-check_backendx","ipam":{}}'
  'networks.egress (external or renamed)|.networks.egress.name = "some-other-network"'
  'networks.egress (external or renamed)|.networks.egress.external = true'
  'networks.backend (external or renamed)|.networks.backend.name = "bridge"'
)
for v in shared loopback lan; do
  case "$v" in shared) base="$shared" ;; loopback) base="$loopback" ;; *) base="$lan" ;; esac
  for e in "${deny[@]}"; do
    expect_refused "$v" "$base" "${e#*|}" "${e%%|*}"
  done
  # the allow-list is not a refuse-everything: an extension key and the files as they are pass
  expect_accepted "$v" "$base" '.["x-note"] = {"a":1}'
  expect_accepted "$v" "$base" '.'
  # The allow-list itself passes the shipped files through the real-render check (no bridge overlay).
  expect_accepted real "$base" '.'
done
# A refused variable's name and value are never printed (an env_file of a secret file puts the secret
# in either, #610).
expect_refused shared "$shared" '.services.worker.environment["name-canary-610"] = "value-canary-610"' 'services.worker.environment: 1 variable'
if grep -qE 'name-canary-610|value-canary-610' "$tmp/refuse.log"; then
  echo "check-prod-compose: self-test: a refused variable's name or value was printed" >&2; exit 1
fi

# The client secret's value (the canary in the dummy file) anywhere in the rendered file.
expect_refused shared "$shared" ".services.web.labels = {\"note\": \"$oauth_canary-google\"}"
for m in \
  '.services.web.environment.CLASSBEAT_OAUTH_GOOGLE_CLIENT_ID = "a-literal-in-compose"' \
  '.services.web.environment.CLASSBEAT_OAUTH_VISIBLE_PROVIDERS = "google,microsoft"' \
  'del(.services.web.environment.CLASSBEAT_OAUTH_MICROSOFT_CLIENT_ID)'; do
  expect_refused oauth_defaults "$defaults" "$m"
done
# The dev compose.yaml as it is (#521): the same rule as the production files.
check_oauth_files "$root" compose.yaml || exit 1
# The compose files as written (copies; the checkout's files are never changed).
mkdir "$tmp/files"
expect_files_refused() {
  cp "$tmp/compose.prod.yaml" "$tmp/compose.prod.loopback.yaml" "$root/compose.yaml" "$tmp/files/"
  sed -i -E "$2" "$tmp/files/$1"
  if (check_oauth_files "$tmp/files" compose.prod.yaml compose.prod.loopback.yaml compose.yaml 2>/dev/null); then
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
# shellcheck disable=SC2016  # a literal compose variable, not a shell one
expect_files_refused compose.yaml \
  's|^(      CLASSBEAT_OAUTH_VISIBLE_PROVIDERS:.*)$|\1\n      CLASSBEAT_OAUTH_GOOGLE_CLIENT_SECRET: ${OAUTH_GOOGLE_CLIENT_SECRET:-}|'
expect_files_refused compose.yaml 's|file: \./secrets/oauth_github_client_secret|environment: OAUTH_GITHUB_CLIENT_SECRET|'
# The same bypasses as YAML (#517): a compose file merged after compose.prod.yaml (and after each
# overlay), rendered with every profile and run through the whole variant check, on every variant. The
# text rule is bypassed on purpose here (expect_yaml_refused looks at the render only), so the
# allow-list is what refuses; expect_text_refused then checks the keys the render drops.
mkdir "$tmp/yaml"
ln -s ../secrets "$tmp/yaml/secrets"
cp "$tmp/compose.prod.yaml" "$tmp/compose.prod.loopback.yaml" "$tmp/compose.prod.lan.yaml" "$tmp/yaml/"
ydir="$native/${tmp##*/}/yaml"
yaml_render() {   # yaml_render VARIANT: the variant's files plus mutation.yaml, every profile
  case "$1" in
    shared) render -f "$ydir/compose.prod.yaml" -f "$ydir/mutation.yaml" ;;
    loopback) render -f "$ydir/compose.prod.yaml" -f "$ydir/compose.prod.loopback.yaml" -f "$ydir/mutation.yaml" ;;
    *) render -f "$ydir/compose.prod.yaml" -f "$ydir/compose.prod.lan.yaml" -f "$ydir/mutation.yaml" ;;
  esac
}
yaml_verdict() {   # yaml_verdict WANT(accepted|refused) FRAGMENT TEXT
  local want="$1" why="$2" v out rc
  printf '%s\n' "$3" > "$tmp/yaml/mutation.yaml"
  for v in shared loopback lan; do
    out="$(yaml_render "$v")" \
      || { echo "check-prod-compose: self-test: a YAML mutation does not render ($v): $3" >&2; exit 1; }
    rc=0; (J="$out"; "check_$v" 2>"$tmp/yaml-refuse.log") || rc=$?
    if [[ "$want" == accepted && "$rc" != 0 ]]; then
      cat "$tmp/yaml-refuse.log" >&2; echo "check-prod-compose: self-test: refused a harmless YAML file ($v): $3" >&2; exit 1
    fi
    if [[ "$want" == refused ]]; then
      [[ "$rc" != 0 ]] || { echo "check-prod-compose: self-test: accepted unsafe YAML ($v): $3" >&2; exit 1; }
      grep -qF -- "$why" "$tmp/yaml-refuse.log" \
        || { cat "$tmp/yaml-refuse.log" >&2; echo "check-prod-compose: self-test: refused for another reason than '$why' ($v): $3" >&2; exit 1; }
    fi
  done
}
expect_yaml_refused() { yaml_verdict refused "$1" "$2"; }   # expect_yaml_refused FRAGMENT TEXT
expect_text_refused() {   # expect_text_refused TEXT: the files' text, for the keys the render drops
  printf '%s\n' "$1" > "$tmp/yaml/mutation.yaml"
  if (check_prod_file_text "$tmp/yaml" mutation.yaml 2>/dev/null); then
    echo "check-prod-compose: self-test: the text rule accepted: $1" >&2; exit 1
  fi
}
expect_yaml_refused configs 'configs:
  hostproc:
    file: /proc
services:
  worker:
    configs:
      - source: hostproc
        target: /hostproc'
expect_yaml_refused services.web.post_start 'services:
  web:
    post_start:
      - command: ["sh", "-c", "id"]
        user: root
        privileged: true'
expect_yaml_refused services.worker.pre_stop 'services:
  worker:
    pre_stop:
      - command: ["sh", "-c", "id"]'
expect_yaml_refused services.worker.volumes_from 'services:
  worker:
    volumes_from: [web]'
expect_yaml_refused volumes.db-data.driver_opts 'volumes:
  db-data:
    driver_opts: {type: none, o: bind, device: /}'
expect_yaml_refused services.worker.pid 'services:
  worker:
    pid: "service:web"'
expect_yaml_refused services.worker.volumes 'services:
  worker:
    volumes:
      - type: bind
        source: /proc/1/root
        target: /h'
expect_yaml_refused services.worker.stop_signal 'services:
  worker:
    stop_signal: SIGKILL'
expect_yaml_refused services.worker.cgroup_parent 'services:
  worker:
    cgroup_parent: elsewhere'
expect_yaml_refused 'security_opt is exactly' 'services:
  worker:
    security_opt: [seccomp=unconfined]'
# B5, both ways: the render holds a profiled service (--profile "*"), as a new service, and the text
# rule refuses the key itself.
expect_yaml_refused services.spy 'services:
  spy:
    image: busybox
    profiles: [ops]
    pid: host
    privileged: true
    group_add: ["10010"]
    logging: {driver: json-file, options: {max-size: 10m}}
    volumes:
      - /:/host'
printf 'services:\n  spy:\n    image: busybox\n    profiles: [ops]\n' > "$tmp/yaml/mutation.yaml"
jq -e '.services | has("spy")' <<<"$(yaml_render shared)" >/dev/null \
  || { echo "check-prod-compose: self-test: the render left out a profiled service" >&2; exit 1; }
expect_yaml_refused services.brand_new_service 'services:
  brand_new_service:
    image: busybox
    logging: {driver: json-file, options: {max-size: 10m}}'
# The text rule's bypasses (#598 review, finding 1; #610): env_file, label_file, profiles and include
# spelled so a line match misses them (a quoted key, YAML's explicit `? key`, a flow mapping). The render
# is the check: the env_file's variables land in environment (check_keys refuses them by name, without
# printing one), the other keys stay in the render or bring a new service. The secret file's canary (an
# OAuth dummy, one line without `=`) becomes the variable "" and must never be printed.
printf 'SPY=1\n' > "$tmp/yaml/spy.env"
printf 'services:\n  spy:\n    image: busybox\n    logging: {driver: json-file, options: {max-size: 10m}}\n' > "$tmp/yaml/other.yaml"
expect_yaml_refused 'services.worker.environment: 1 variable' 'services:
  worker:
    "env_file": ./secrets/oauth_google_client_secret'
grep -qF -- "$oauth_canary" "$tmp/yaml-refuse.log" \
  && { echo "check-prod-compose: self-test: a refused env_file's value was printed" >&2; exit 1; }
expect_yaml_refused 'services.worker.environment: 1 variable' "services:
  worker:
    'env_file': [./spy.env]"
expect_yaml_refused 'services.worker.environment: 1 variable' 'services:
  worker:
    ? env_file
    : ./spy.env'
expect_yaml_refused 'services.db.environment: 1 variable' 'services:
  db:
    ? env_file
    : ./secrets/connection_kek'
grep -qF -- 'dummy-secret-connection_kek' "$tmp/yaml-refuse.log" \
  && { echo "check-prod-compose: self-test: a refused env_file's value was printed" >&2; exit 1; }
expect_yaml_refused 'services.web.environment: 1 variable' 'services: {web: {"env_file": [{path: ./spy.env}]}}'
# (Compose versions differ in what they keep: label_file itself, or only the labels it reads.)
expect_yaml_refused 'services.worker.label' 'services:
  worker:
    ? label_file
    : ./spy.env'
expect_yaml_refused 'services.spy' 'services:
  spy:
    image: busybox
    logging: {driver: json-file, options: {max-size: 10m}}
    "profiles": [ops]'
expect_yaml_refused 'services.spy' '? include
: [./other.yaml]'
# The real render (check_real, #610): the documented compose.bridge.yaml of proton-bridge-smtp.md passes;
# any other shape of it, and anything else the dummy files would refuse, does not.
mkdir -p "$tmp/yaml/smtp-ca"; : > "$tmp/yaml/smtp-ca/bridge.pem"
real_verdict() {   # real_verdict WANT(accepted|refused) FRAGMENT TEXT: shared and loopback, plus mutation.yaml
  local want="$1" why="$2" v out rc
  printf '%s\n' "$3" > "$tmp/yaml/mutation.yaml"
  for v in shared loopback lan; do
    out="$(yaml_render "$v")" \
      || { echo "check-prod-compose: self-test: a real-render mutation does not render ($v): $3" >&2; exit 1; }
    rc=0; (J="$out"; check_real 2>"$tmp/real-refuse.log") || rc=$?
    if [[ "$want" == accepted && "$rc" != 0 ]]; then
      cat "$tmp/real-refuse.log" >&2; echo "check-prod-compose: self-test: the real-render check refused ($v): $3" >&2; exit 1
    fi
    if [[ "$want" == refused ]]; then
      [[ "$rc" != 0 ]] || { echo "check-prod-compose: self-test: the real-render check accepted ($v): $3" >&2; exit 1; }
      grep -qF -- "$why" "$tmp/real-refuse.log" \
        || { cat "$tmp/real-refuse.log" >&2; echo "check-prod-compose: self-test: real render refused for another reason than '$why' ($v): $3" >&2; exit 1; }
    fi
  done
}
bridge='services:
  web:
    volumes:
      - ./smtp-ca/bridge.pem:/run/smtp-ca/bridge.pem:ro
    networks:
      - bridge-mail
networks:
  bridge-mail:
    external: true
    name: proton-bridge-net'
real_verdict accepted '' "$bridge"
real_verdict refused services.web.volumes "${bridge/:ro/}"
real_verdict refused services.web.volumes "${bridge/.\/smtp-ca\/bridge.pem:/.\/secrets:}"
real_verdict refused services.web.volumes "${bridge/\/run\/smtp-ca\/bridge.pem/\/run\/secrets\/x}"
real_verdict refused services.web.volumes "${bridge/external: true/external: false}"
real_verdict refused services.web.volumes "${bridge/services:/services:
  worker:
    networks: [bridge-mail]}"
real_verdict refused services.web.volumes "${bridge//bridge-mail/mail-net}"
real_verdict refused 'networks.spynet' 'services:
  web:
    networks: [spynet]
networks:
  spynet:
    external: true
    name: somebody-elses-network'
real_verdict refused 'services.web.environment: 1 variable' "${bridge/  web:/  web:
    \"env_file\": ./secrets/oauth_github_client_secret}"
real_verdict refused 'services.worker.environment: 1 variable' 'services:
  worker:
    ? env_file
    : ./spy.env'
expect_refused real "$shared" '.services.db.volumes += [{"type":"bind","source":"/proc","target":"/h"}]' 'no bind mounts'
expect_refused real "$shared" '.services.db.user = "0"' 'runs as root'
real_verdict refused 'services.spy' 'services:
  spy:
    image: busybox
    profiles: [ops]'
# Harmless: an extension key, and a comment that names a forbidden key, pass both rules.
yaml_verdict accepted "" 'x-note: fine'
printf '# profiles: [x] and env_file: y in a comment\nx-note: fine\n' > "$tmp/yaml/mutation.yaml"
check_prod_file_text "$tmp/yaml" mutation.yaml \
  || { echo "check-prod-compose: self-test: the text rule refused a comment" >&2; exit 1; }
# The keys the render drops (and the two that pull in text this script never sees), in the files' text.
expect_text_refused 'services:
  worker:
    env_file: ./secrets/app_secret_key'
expect_text_refused 'services:
  worker:
    env_file:
      - path: ./secrets/connection_kek
        required: false'
expect_text_refused 'services:
  worker:
    label_file: ./secrets/app_secret_key'
expect_text_refused 'services:
  spy:
    image: busybox
    profiles: [ops]'
expect_text_refused 'services:
  spy: {image: busybox, profiles: [ops]}'
expect_text_refused 'services:
  worker:
    "env_file": [./secrets/app_secret_key]'
expect_text_refused 'services: {worker: {env_file: [./secrets/app_secret_key]}}'
expect_text_refused 'include:
  - ./other.yaml'
expect_text_refused 'services:
  worker:
    extends:
      file: other.yaml
      service: web'
# The same, written into each real file (copies): none of the three may carry one.
mkdir "$tmp/textfiles"
for f in compose.prod.yaml compose.prod.loopback.yaml compose.prod.lan.yaml; do
  for key in 'env_file: ./secrets/app_secret_key' 'label_file: ./secrets/app_secret_key' 'profiles: [ops]'; do
    cp "$tmp/compose.prod.yaml" "$tmp/compose.prod.loopback.yaml" "$tmp/compose.prod.lan.yaml" "$tmp/textfiles/"
    sed -i -E "0,/^services:/s||services:\n  web:\n    $key|" "$tmp/textfiles/$f"
    if (check_prod_file_text "$tmp/textfiles" 2>/dev/null); then
      echo "check-prod-compose: self-test: accepted $f with '$key'" >&2; exit 1
    fi
  done
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
# swag is this server (PROXY_IP equal to WEB_LAN_ADDRESS): the web setting matches PROXY_IP, so only the
# clause `$ip != $lan` of check_lan can refuse it (the mutation list above is refused by the first clause
# alone: a mutation removing `$ip != $lan` survived, review of #576).
lan_same="$(PROXY_IP="$lan_ip" render -f "$dir/compose.prod.yaml" -f "$dir/compose.prod.lan.yaml")"
swag_keep="$swag_ip"; swag_ip="$lan_ip"
expect_refused lan "$lan_same" '.' "and not this server"
swag_ip="$swag_keep"
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
  owner_trusted="$value"
  proxy_ip_ok "$value" || fail "the real PROXY_IP must be one private IPv4 address (swag's), not a range, list or name" \
    || return 1
}
rc=0; check_owner_proxy_ip "$env_file" "$owner_proxy_ip_set" "$owner_proxy_ip" || rc=$?
case "$rc" in
  0) owner_note="the real PROXY_IP is one private address" ;;
  2) owner_note="no real PROXY_IP set here (only dummies checked)" ;;
  *) exit 1 ;;
esac

# The real PROXY_IP against the address swag really has (#500): a typo such as .130 for .10 is inside
# Docker's allocation range, so the form check above passes it while web would trust nobody.
# swag_address_verdict PROXY_IP "ADDRESS..." (swag's addresses on its networks, empty when the
# container is not visible): 0 the address is one of them, 1 it is not, 2 nothing to compare.
swag_address_verdict() {
  local want="$1" have="$2" a
  [[ -n "${have//[[:space:]]/}" ]] || return 2
  for a in $have; do [[ "$a" == "$want" ]] && return 0; done
  fail "PROXY_IP is $want but the running swag container has ${have% }: set PROXY_IP to swag's address (runbook section 6a)" || return 1
}
# The running swag container's addresses (no output when it is not running or Docker cannot be asked).
swag_addresses() {
  local name="${PROD_CHECK_SWAG_CONTAINER:-swag}"
  [[ "$name" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]] || { fail "PROD_CHECK_SWAG_CONTAINER is not a container name"; return 1; }
  docker inspect --format '{{if .State.Running}}{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}{{end}}' \
    "$name" 2>/dev/null || true
}

# Where the real stack publishes web (install mode): only web publishes a port, and only on loopback
# (the host's own proxy) or on one private LAN address (compose.prod.lan.yaml, swag on another host),
# never on every interface; swag is not this server (#567).
check_real_ports() {   # check_real_ports RENDERED_JSON
  local j="$1" host trusted
  jq -e '[.services | to_entries[] | select(.key != "web") | select(.value.ports // [] | length > 0)] | length == 0' <<<"$j" >/dev/null \
    || fail "only web may publish a port" || return 1
  # As tight as the shipped overlays (check_loopback, check_lan): at most one port, tcp, to web's 8000.
  # Both overlays together, or a second port from an overlay of the owner's own, would publish web twice
  # (#470, review of #576). The host port may differ from 8080 (WEB_HOST_PORT).
  jq -e '(.services.web.ports // []) | length <= 1 and all(.target == 8000 and (.protocol // "tcp") == "tcp")' <<<"$j" >/dev/null \
    || fail "web publishes at most one port (container port 8000, tcp): use one overlay, not both (runbook section 6 or 6c)" || return 1
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
  ( J="$real_all_json"; check_real ) \
    || { echo "check-prod-compose: the real render (your .env, COMPOSE_FILE and every profile) is refused (above)" >&2; exit 1; }
  # Only where web trusts PROXY_IP itself: not under the loopback overlay (the web-out gateway) and
  # not with a published port (the LAN overlay: swag is on another host).
  if [[ -n "${owner_trusted:-}" ]]; then
    if [[ "$(jq -r '.services.web.environment.CLASSBEAT_TRUSTED_PROXIES // ""' <<<"$real_json")" == "$owner_trusted" ]] \
        && jq -e '.services.web.ports // [] | length == 0' <<<"$real_json" >/dev/null; then
      rc=0; swag_address_verdict "$owner_trusted" "$(swag_addresses)" || rc=$?
      case "$rc" in
        0) owner_note="$owner_note; PROXY_IP is the running swag container's address" ;;
        2) owner_note="$owner_note; the swag container is not visible here: compare PROXY_IP with \`docker inspect\` by hand (runbook section 9)" ;;
        *) exit 1 ;;
      esac
    else
      owner_note="$owner_note; swag is not on the shared network of this host: PROXY_IP not compared with its address"
    fi
  fi
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

# swag's address against PROXY_IP (#500): a match, a typo inside Docker's range, a second network,
# and nothing to compare.
swag_address_verdict 172.29.90.10 "172.29.90.10 " 2>/dev/null || { echo "check-prod-compose: self-test: swag's own address refused" >&2; exit 1; }
swag_address_verdict 172.29.90.10 "10.1.0.5 172.29.90.10 " 2>/dev/null || { echo "check-prod-compose: self-test: swag's address on its second network refused" >&2; exit 1; }
if (swag_address_verdict 172.29.90.130 "172.29.90.10 " 2>/dev/null); then
  echo "check-prod-compose: self-test: a PROXY_IP that is not swag's address was accepted" >&2; exit 1
fi
rc=0; swag_address_verdict 172.29.90.10 "  " 2>/dev/null || rc=$?
[[ "$rc" == 2 ]] || { echo "check-prod-compose: self-test: no swag address must be 'nothing to compare', not a verdict" >&2; exit 1; }
fi

# --- The swag site config: nginx's directives, not its lines; then nginx's own syntax check -------
# #500: the first version counted lines, and four spellings of a second `proxy_set_header
# X-Forwarded-For` got past it (a second directive on the line of another, one split over two lines,
# one after a quoted "#" the comment stripper ate, one inside a one-line location). So the file is
# read the way nginx reads it (site_directives, a tokenizer for ngx_conf_read_token's rules) and the
# rules (site_verdict) look at directives and the block each one is in. Why not `nginx -T`: it dumps
# the files as written, comments and layout intact, so it normalizes nothing; and the tokenizer needs
# no Docker, which the owner's pre-deploy run may not reach. `nginx -t` still runs on the same file.
# This is a best-effort lint of honest mistakes, like the compose checks (see the header).
site_label="$site"
sfail() { echo "check-prod-compose: $site_label: $*" >&2; return 1; }

# site_directives FILE: one tab-separated record per token run, in file order:
#   D<TAB>ctx<TAB>name<TAB>arg...   a directive (ctx: empty, server, server/location)
#   B<TAB>ctx<TAB>name<TAB>arg...   a block opens (only `server`, then `location` inside it)
#   E<TAB>ctx                       a block closed (ctx: where we are now)
#   ERR<TAB>message                 not readable (exit 1)
# Tokens as nginx reads them: whitespace separates; `#` starts a comment only at the start of a token;
# '...' and "..." quote (so `;` `{` `#` inside are text) and must be followed by a delimiter; a
# backslash makes the next character plain; `${name}` is `$name` and its `{` opens no block; `}`
# inside a token is text; `;` ends a directive, `{` opens a block, `}` closes it. Tokens are unquoted
# and their tabs and newlines become spaces.
# shellcheck disable=SC2016  # awk's $ variables, not shell ones
site_directives() {
  timeout 30 awk '
    function err(m,   t, ln) { t = substr(s, 1, i); ln = gsub(/\n/, "", t) + 1; print "ERR\tline " ln ": " m; failed = 1; exit 1 }
    function emit(kind,   line, k) {
      line = kind "\t" ctx
      for (k = 1; k <= nt; k++) line = line "\t" tk[k]
      print line; nt = 0
    }
    function esc(d) { return (d == "\"" || d == "\047" || d == "\\") ? d : "\\" d }
    { s = s $0 "\n" }
    END {
      n = length(s); i = 1; nt = 0; depth = 0; ctx = ""
      while (i <= n) {
        c = substr(s, i, 1)
        if (c == " " || c == "\t" || c == "\n" || c == "\r") { i++; continue }
        if (c == "#") { while (i <= n && substr(s, i, 1) != "\n") i++; continue }
        if (c == ";") { if (nt == 0) err("unexpected ;"); emit("D"); i++; continue }
        if (c == "{") {
          if (nt == 0) err("unexpected {")
          if (depth == 0 && !(tk[1] == "server" && nt == 1)) err("only a server block is read at the top of this file")
          if (depth == 1 && tk[1] != "location") err("only location blocks are read inside the server")
          if (depth >= 2) err("no block inside a location")
          emit("B"); depth++; ctx = (depth == 1 ? "server" : "server/location"); i++; continue
        }
        if (c == "}") {
          if (nt > 0) err("missing ; before }")
          if (depth == 0) err("unexpected }")
          depth--; ctx = (depth == 0 ? "" : "server"); print "E\t" ctx; i++; continue
        }
        tok = ""
        if (c == "\"" || c == "\047") {
          q = c; i++; closed = 0
          while (i <= n) {
            c = substr(s, i, 1)
            if (c == "\\") { d = substr(s, i + 1, 1); tok = tok esc(d); i += 2; continue }
            if (c == q) { closed = 1; i++; break }
            tok = tok c; i++
          }
          if (!closed) err("unterminated quote")
          if (i <= n && substr(s, i, 1) !~ /[ \t\r\n;{)]/) err("unexpected character after a closing quote")
        } else {
          v = 0
          while (i <= n) {
            c = substr(s, i, 1)
            if (c == "\\") { d = substr(s, i + 1, 1); tok = tok esc(d); i += 2; v = 0; continue }
            if (c == " " || c == "\t" || c == "\n" || c == "\r" || c == ";") break
            if (c == "{") { if (v) { tok = tok c; i++; continue } break }
            v = (c == "$"); tok = tok c; i++
          }
        }
        while (match(tok, /\$\{[A-Za-z0-9_]+\}/)) tok = substr(tok, 1, RSTART - 1) "$" substr(tok, RSTART + 2, RLENGTH - 3) substr(tok, RSTART + RLENGTH)
        gsub(/[\t\r\n]/, " ", tok)
        tk[++nt] = tok
      }
      if (nt > 0) err("missing ; at the end")
      if (depth > 0) err("missing } at the end")
    }
  ' "$1"
}

# site_verdict LIVE: reads site_directives' records; prints each broken rule, exits 1 if any.
# LIVE=1 (the owner's file) lets the Cloudflare include and real_ip_header both be absent (DNS only,
# runbook section 8); the sample must have both.
# shellcheck disable=SC2016  # literal nginx variables, not shell ones
site_verdict() {
  timeout 30 awk -F'\t' -v live="$1" '
    BEGIN { rih = 0; cfinc = 0 }
    function bad(m) { print m; nbad++ }
    function fin() { if (inloc) { if (sse && !buf) bad("the SSE location needs proxy_buffering off"); if (sse && !rt) bad("the SSE location needs proxy_read_timeout of at least 900s") } inloc = 0 }
    { kind = $1; ctx = $2; name = $3; a1 = tolower($4); args = ""
      for (k = 4; k <= NF; k++) args = args (k > 4 ? " " : "") $k }
    kind == "B" {
      if (name == "server") nserver++
      else { fin(); inloc = 1; buf = 0; rt = 0; sse = (index(args, "events") > 0) }
      next
    }
    kind == "E" { fin(); next }
    kind != "D" { next }
    index(args, "proxy_add_x_forwarded_for") { bad("never append to a visitor X-Forwarded-For (no proxy_add_x_forwarded_for)") }
    name == "proxy_set_header" {
      if (ctx == "server/location") bad("a location sets proxy_set_header (it replaces every inherited one, X-Forwarded-For included): set none there")
      if (a1 == "x-forwarded-for") {
        nxff++
        if (ctx != "server" || NF != 5 || $5 != "$remote_addr") badxff = 1
      }
    }
    name == "add_header" {
      if (ctx == "server/location") bad("a location sets add_header (it replaces every inherited one): set none there")
      if (a1 == "content-security-policy" || a1 == "referrer-policy") bad("the app CSP and Referrer-Policy are neither added, hidden nor replaced here")
      if (a1 == "strict-transport-security" && ctx == "server") hsts = 1
    }
    name == "proxy_hide_header" || name == "more_clear_headers" || name == "more_set_headers" { bad("the app CSP and Referrer-Policy are neither added, hidden nor replaced here") }
    name == "include" {
      if (ctx != "server" || NF != 4 || ($4 != "/config/nginx/ssl.conf" && $4 != "/config/nginx/cf_real-ip.conf")) bad("include only /config/nginx/ssl.conf and cf_real-ip.conf, in the server")
      if ($4 == "/config/nginx/cf_real-ip.conf") cfinc = 1
    }
    name == "client_max_body_size" { nbody++; if (ctx != "server" || NF != 4 || $4 != "1m") bad("client_max_body_size 1m in the server only (the app cap is 64 KiB)") }
    name == "server_name" { nsn++; if (ctx != "server" || NF != 4 || $4 !~ /^[a-z0-9.-]+$/) bad("server_name is the exact host") }
    name == "server_tokens" { if (NF != 4 || a1 != "off") bad("no server_tokens on"); else if (ctx == "server") sto = 1 }
    name == "proxy_intercept_errors" && a1 != "off" { bad("the app own errors pass through (no proxy_intercept_errors)") }
    name == "real_ip_header" { if (ctx == "server" && NF == 4 && $4 == "CF-Connecting-IP") rih = 1; else bad("real IP from CF-Connecting-IP (Cloudflare ranges only)") }
    name == "real_ip_recursive" && a1 != "off" { bad("Cloudflare ranges belong in cf_real-ip.conf only, non-recursive") }
    name == "set_real_ip_from" { bad("Cloudflare ranges belong in cf_real-ip.conf only, non-recursive") }
    ctx == "server/location" && name == "proxy_buffering" && a1 == "off" { buf = 1 }
    ctx == "server/location" && name == "proxy_read_timeout" && $4 ~ /^(9[0-9][0-9]|[1-9][0-9][0-9][0-9]+)s$/ { rt = 1 }
    END {
      if (nserver != 1) bad("exactly one server block (found " nserver + 0 ")")
      if (nxff != 1) bad("exactly one proxy_set_header X-Forwarded-For (found " nxff + 0 ")")
      else if (badxff) bad("X-Forwarded-For must be overwritten with $remote_addr, once, in the server and in no location")
      if (nbody != 1) bad("client_max_body_size 1m (found " nbody + 0 " directives)")
      if (nsn != 1) bad("exactly one server_name, the exact host (found " nsn + 0 ")")
      if (!sto) bad("server_tokens off in the server (no nginx version)")
      if (!hsts) bad("add_header Strict-Transport-Security in the server (HSTS is the edge job)")
      if (live ? (rih != cfinc) : !(rih && cfinc)) bad("real IP: include /config/nginx/cf_real-ip.conf and real_ip_header CF-Connecting-IP go together" (live ? " (both or neither with DNS only)" : ""))
      exit (nbad > 0)
    }
  '
}
check_site() {   # check_site FILE [live]
  local dirs msgs l rc=0
  # Both awk programs run under `timeout 30` (exit 124): a loop in them must fail the check, not hang it.
  dirs="$(site_directives "$1")" || rc=$?
  if (( rc == 124 )); then sfail "reading it as nginx directives took more than 30 seconds"; return 1; fi
  if (( rc != 0 )); then
    msgs="${dirs##*$'\n'}"; sfail "cannot read it as nginx directives: ${msgs#ERR$'\t'}"; return 1
  fi
  rc=0
  msgs="$(site_verdict "${2:+1}" <<<"$dirs")" || rc=$?
  if (( rc == 124 )); then sfail "checking its directives took more than 30 seconds"; return 1; fi
  if (( rc != 0 )); then
    while IFS= read -r l; do sfail "$l" || true; done <<<"$msgs"
    return 1
  fi
}

# nginx_log_summary LOG: what a failed `nginx -t` said, without its words (#470, from the review of
# #592). nginx quotes tokens of the file in its messages, and on the owner's live file a token can be
# a secret, so the log itself is never printed: per error line only its level (one of a fixed set)
# and, for the site file, the line number (digits only), at most 5 lines, then a count. Nothing else
# of the log or the file reaches the output.
nginx_log_summary() {   # nginx_log_summary LOG
  awk '
    match($0, /\[(emerg|alert|crit|error|warn)\]/) {
      n++
      if (n > 5) next
      level = substr($0, RSTART + 1, RLENGTH - 2)
      where = "no site config line"
      if ($0 ~ /classbeat\.subdomain\.conf:[0-9]+$/) {
        line = $0; sub(/.*:/, "", line)
        where = "line " substr(line, 1, 7) " of the site config"
      }
      print "nginx -t: " level " at " where
    }
    END {
      if (n > 5) print "nginx -t: " (n - 5) " more error lines"
      if (n == 0) print "nginx -t: no nginx error line (is the Docker daemon running and the nginx image pullable? runbook section 9)"
    }' "$1" | while IFS= read -r l; do sfail "$l" || true; done
}

# nginx -t on FILE with stand-ins for swag's ssl.conf and the Cloudflare list. Needs the Docker daemon
# and the nginx image (pulled from Docker Hub on first use, runbook section 9). On a failure only
# nginx_log_summary is printed, never the log.
nginx_site_t() {   # nginx_site_t FILE
  mkdir -p "$tmp/nginx"
  printf 'ssl_certificate /tmp/cert.pem;\nssl_certificate_key /tmp/key.pem;\n' > "$tmp/nginx/ssl.conf"
  printf 'set_real_ip_from 173.245.48.0/20;\n' > "$tmp/nginx/cf_real-ip.conf"
  printf 'events {}\nhttp {\n  resolver 127.0.0.11;\n  include /config/nginx/proxy-confs/classbeat.subdomain.conf;\n}\n' > "$tmp/nginx/nginx.conf"
  cp "$1" "$tmp/nginx/site.conf" || { sfail "cannot copy it (readable by this user?)"; return 1; }
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
    grep -q 'Address family not supported' "$tmp/nginx.log" || { nginx_log_summary "$tmp/nginx.log"; sfail "nginx -t failed"; return 1; }
    sed -i '/listen \[::\]:443 ssl;/d' "$tmp/nginx/site.conf"
    nginx_t || { nginx_log_summary "$tmp/nginx.log"; sfail "nginx -t failed"; return 1; }
    echo "check-prod-compose: no IPv6 here: nginx -t ran without the [::]:443 listen line" >&2
  fi
}

if [[ $mode == repo ]]; then   # the sample and its self-tests (ADR 0020)
check_site "$site" || exit 1
# expect_site_refused SED [FRAGMENT]: the mutated sample must be refused, and with FRAGMENT in the
# message when given (so a mutation that merely fails to parse cannot pass for the rule it is for).
expect_site_refused() {
  sed -E "$1" "$site" > "$tmp/site-mutated.conf"
  if (check_site "$tmp/site-mutated.conf" 2>"$tmp/site-refuse.log"); then
    echo "check-prod-compose: self-test: accepted an unsafe site config: $1" >&2; exit 1
  fi
  if [[ -n "${2:-}" ]] && ! grep -qF -- "$2" "$tmp/site-refuse.log"; then
    cat "$tmp/site-refuse.log" >&2
    echo "check-prod-compose: self-test: refused for another reason than '$2': $1" >&2; exit 1
  fi
}
# The harmless rewrites: the same directives in another layout must still pass (the tokenizer must
# not see a second directive, or lose one, where nginx sees none).
expect_site_accepted() {
  sed -E "$1" "$site" > "$tmp/site-mutated.conf"
  if ! (check_site "$tmp/site-mutated.conf" 2>"$tmp/site-accept.log"); then
    cat "$tmp/site-accept.log" >&2
    echo "check-prod-compose: self-test: refused a valid site config: $1" >&2; exit 1
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
  's/^( *)location @classbeat_down \{$/\1server { server_name classbeat.example.test; }\n&/' \
  '$a\server { listen 8443; server_name other.example.test; }' \
  '/include \/config\/nginx\/ssl.conf;/a\    include /config/nginx/proxy.conf;' \
  '/include \/config\/nginx\/ssl.conf;/a\    include /config/nginx/extra-headers.conf;' \
  '/server_tokens off;/d' \
  's/server_tokens off;/server_tokens on;/' \
  '/error_page 502/a\    proxy_intercept_errors on;' \
  '/client_max_body_size 1m;/d' \
  '/client_max_body_size 1m;/a\    client_max_body_size 100m;' \
  '/add_header Strict-Transport-Security/a\    add_header Content-Security-Policy "default-src *" always;' \
  's/proxy_buffering off;/proxy_buffering on;/' \
  '/return 502/a\        add_header Cache-Control "no-store" always;' \
  's/real_ip_header CF-Connecting-IP;/real_ip_header X-Forwarded-For;/' \
  '/include \/config\/nginx\/cf_real-ip.conf;/d' \
  's/^( *)location \/ \{$/&\n        if ($host) { return 200; }/' \
  's/proxy_set_header X-Forwarded-For \$remote_addr;/proxy_set_header X-Forwarded-For $remote_addr/'; do
  expect_site_refused "$m"
done
# The four spellings of a second X-Forwarded-For that the line-based check missed (#500), each with
# the visitor-chosen value and with $remote_addr, and a location that carries the header itself.
# shellcheck disable=SC2016  # literal nginx variables, not shell ones
for v in '$http_x_forwarded_for' '$remote_addr'; do
  one='exactly one proxy_set_header X-Forwarded-For (found 2)'
  # (a) a second directive on the line of X-Forwarded-Proto, after the overwrite
  expect_site_refused 's/^( *proxy_set_header X-Forwarded-Proto https;)$/\1 proxy_set_header X-Forwarded-For '"$v"';/' "$one"
  # (b) the directive split over two lines (after its name, and before its value)
  expect_site_refused 's/^( *proxy_set_header X-Forwarded-Proto https;)$/\1\n    proxy_set_header\n        X-Forwarded-For '"$v"';/' "$one"
  expect_site_refused 's/^( *proxy_set_header X-Forwarded-Proto https;)$/\1\n    proxy_set_header X-Forwarded-For\n        '"$v"';/' "$one"
  # (c) after a quoted "#" (and after a "#" inside a token) that a comment stripper would cut at
  expect_site_refused 's/^( *proxy_set_header Host \$host;)$/\1\n    proxy_set_header X-Real-IP "#"; proxy_set_header X-Forwarded-For '"$v"';/' "$one"
  expect_site_refused 's/^( *proxy_set_header Host \$host;)$/\1\n    proxy_set_header X-Real-IP a#b; proxy_set_header X-Forwarded-For '"$v"';/' "$one"
  # (d) a one-line location block, and a multi-line one: a location's own proxy_set_header replaces
  # every inherited one, so it is refused whatever it sets
  expect_site_refused 's/^( *)location @classbeat_down \{$/\1location \/api\/ { proxy_set_header X-Forwarded-For '"$v"'; proxy_pass http:\/\/$upstream_classbeat:8000; }\n&/' \
    'a location sets proxy_set_header'
  expect_site_refused 's/^( *)location \/ \{$/&\n        proxy_set_header\n            X-Forwarded-For '"$v"';/' \
    'a location sets proxy_set_header'
done
# A backslash inside a quoted string (#470, review of #592): `\\"` ends the string after one backslash
# (sed turns the four backslashes below into two, nginx reads two as one), so the second
# X-Forwarded-For after it is a real directive.
# shellcheck disable=SC2016  # literal nginx variables, not shell ones
expect_site_refused 's/^( *proxy_set_header Host \$host;)$/\1\n    proxy_set_header X-Real-IP "a\\\\"; proxy_set_header X-Forwarded-For $http_x_forwarded_for;/' \
  'exactly one proxy_set_header X-Forwarded-For (found 2)'
# A location that sets some other header drops the server-level overwrite from what it forwards.
# shellcheck disable=SC2016  # literal nginx variables, not shell ones
expect_site_refused 's/^( *)location @classbeat_down \{$/\1location \/api\/ { proxy_set_header Host $host; proxy_pass http:\/\/$upstream_classbeat:8000; }\n&/' \
  'a location sets proxy_set_header'
# shellcheck disable=SC2016  # literal nginx variables, not shell ones
for m in \
  's/proxy_set_header X-Forwarded-For \$remote_addr;/proxy_set_header\n        "x-forwarded-for"\n        ${remote_addr}; # proxy_set_header X-Forwarded-For $http_x_forwarded_for;/' \
  's/^( *proxy_set_header Host \$host;)$/\1\n    proxy_set_header X-Real-IP "a # b ; proxy_set_header X-Forwarded-For x"; proxy_set_header X-Real-Host a#b;/' \
  's/^( *)location @classbeat_down \{$/\1location = \/health { return 200 "ok"; }\n&/' \
  's/^( *proxy_set_header X-Forwarded-Proto https;)$/\1 # proxy_set_header X-Forwarded-For $http_x_forwarded_for;/' \
  's/^( *proxy_set_header Host \$host;)$/\1\n    proxy_set_header X-Real-IP "a \\" ; proxy_set_header X-Forwarded-For x";/' \
  's/^( *proxy_set_header Host \$host;)$/\1\n    proxy_set_header X-Real-IP a\\;proxy_set_header X-Forwarded-For x;/'; do
  expect_site_accepted "$m"
done
# nginx_log_summary prints no word of the log (#470): a planted canary in every place nginx quotes
# file content must not appear; the level and the site file's line number must.
canary="CANARY-$RANDOM$RANDOM-not-to-be-printed"
{
  printf 'nginx: [emerg] unknown directive "%s" in /config/nginx/proxy-confs/classbeat.subdomain.conf:12\n' "$canary"
  printf 'nginx: [warn] "%s" in /config/nginx/ssl.conf:3\n' "$canary"
  printf '%s\n' "$canary"
  printf 'nginx: configuration file /check/nginx.conf test failed (%s)\n' "$canary"
} > "$tmp/nginx-canary.log"
nginx_log_summary "$tmp/nginx-canary.log" 2> "$tmp/nginx-summary.log"
if grep -qF -- "$canary" "$tmp/nginx-summary.log" \
  || ! grep -qF 'nginx -t: emerg at line 12 of the site config' "$tmp/nginx-summary.log" \
  || ! grep -qF 'nginx -t: warn at no site config line' "$tmp/nginx-summary.log" \
  || [[ "$(wc -l < "$tmp/nginx-summary.log")" -ne 2 ]]; then
  echo "check-prod-compose: self-test: the nginx -t summary is not the bounded, redacted one" >&2; exit 1
fi
for _ in 1 2 3 4 5 6 7; do printf 'nginx: [emerg] "%s" in /x/classbeat.subdomain.conf:1\n' "$canary"; done > "$tmp/nginx-canary.log"
nginx_log_summary "$tmp/nginx-canary.log" 2> "$tmp/nginx-summary.log"
if grep -qF -- "$canary" "$tmp/nginx-summary.log" || [[ "$(wc -l < "$tmp/nginx-summary.log")" -ne 6 ]] \
  || ! grep -qF 'nginx -t: 2 more error lines' "$tmp/nginx-summary.log"; then
  echo "check-prod-compose: self-test: the nginx -t summary is not bounded to 5 lines" >&2; exit 1
fi
nginx_site_t "$site" || exit 1
fi

# The owner's own file (PROD_CHECK_SWAG_CONF, runbook sections 7 and 9): the same rules and nginx -t.
if [[ -n "$swag_live" ]]; then
  site_label="$swag_live"
  check_site "$swag_live" live || exit 1
  nginx_site_t "$swag_live" || exit 1
  owner_note="$owner_note; the swag site config $swag_live passes the directive rules and nginx -t"
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

if [[ $mode == install ]]; then
  echo "install check (compose files with dummies, the real render on the allow-list, .env, secrets/): ok; $owner_note"
  exit 0
fi
echo "compose.prod.yaml (shared network, loopback and LAN, default-deny keys, variables and networks, OAuth secrets on web only, secret files by group 10010), runbook network, swag site config and self-tests: ok; $owner_note"
