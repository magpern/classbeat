# shellcheck shell=bash
# Setup: the temp folder, the dummy values, the real .env render in install mode, and the dummy renders.
# Sourced by check-prod-compose.sh, never run on its own; it shares that script's shell, variables and functions. Part of the check-prod-compose split (#763): no check lives here that is not in the original.
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
  app_secret_key connection_kek smtp_password anthropic_api_key openai_api_key deepseek_api_key swedebeat_app \
  turnstile_secret_key; do
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
# Likewise Turnstile (ADR 0025 Amendment 2): a stray TURNSTILE_ENABLED in the shell must not change the default the rules pin to "true".
unset TURNSTILE_ENABLED TURNSTILE_SITE_KEY
# And the MOWL QR switch (ADR 0028): the default the rules pin is "true", whatever the shell exports.
unset MOWL_QR_ENABLED
defaults="$(render -f "$dir/compose.prod.yaml")"
# Dummy client ids (public values, not secret): web's settings must equal them, so they come from
# the variables and from nothing written into the compose files.
export OAUTH_GOOGLE_CLIENT_ID=check-google-client-id OAUTH_GITHUB_CLIENT_ID=check-github-client-id
export OAUTH_MICROSOFT_CLIENT_ID=check-microsoft-client-id OAUTH_VISIBLE_PROVIDERS=google,github
shared="$(render -f "$dir/compose.prod.yaml")"
loopback="$(render -f "$dir/compose.prod.yaml" -f "$dir/compose.prod.loopback.yaml")"
lan="$(render -f "$dir/compose.prod.yaml" -f "$dir/compose.prod.lan.yaml")"

