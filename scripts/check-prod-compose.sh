#!/usr/bin/env bash
# Checks compose.prod.yaml and the Caddyfile with dummy values (issue #341). No server, no secret.
#   scripts/check-prod-compose.sh        (needs docker, jq; run from anywhere)
# 1. `docker compose config` accepts the file; 2. invariants of the production stack hold;
# 3. `caddy validate` accepts the Caddyfile.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"
# Git Bash on Windows: the native docker needs a Windows path (pwd -W); elsewhere it is plain pwd.
native="$(pwd -W 2>/dev/null || pwd)"
tmp="$(mktemp -d "$root/.prod-check.XXXXXX")"
trap 'rm -rf "$tmp"' EXIT

# A copy of the file next to dummy secret files, so the checkout's own secrets/ is never read.
cp compose.prod.yaml "$tmp/"
mkdir "$tmp/secrets"
for n in postgres_password database_owner_password database_password database_maint_password \
  app_secret_key connection_kek smtp_password anthropic_api_key openai_api_key deepseek_api_key swedebeat_app; do
  : > "$tmp/secrets/$n"
done

export CLASSBEAT_VERSION=0.0.0 CLASSBEAT_DOMAIN=classbeat.example.test ACME_EMAIL=ops@example.test
export SMTP_HOST=smtp.example.test SMTP_SENDER=noreply@example.test
export SMTP_CA_FILE=/run/smtp-ca/bridge.pem
export COMPOSE_PROJECT_NAME=classbeat-prod-check
json="$(docker compose -f "$native/${tmp##*/}/compose.prod.yaml" config --format json)"

fail() { echo "check-prod-compose: $*" >&2; exit 1; }
q() { jq -e "$1" <<<"$json" >/dev/null; }

q '[.services | to_entries[] | select(.value.ports // [] | length > 0) | .key] == ["caddy"]' \
  || fail "only caddy may publish ports"
q '.services | has("mailpit") | not' || fail "no mailpit in production"
q '.services.db | (.ports // []) | length == 0' || fail "db must not publish ports"
q '.networks.backend.internal == true' || fail "the backend network must be internal"
q '.services.web.read_only == true' || fail "web must be read-only"
q '[.services.web.secrets[].source] | (index("anthropic_api_key") // -1) < 0' \
  || fail "web must not mount a model key"
q '[.services | to_entries[] | select(.value.secrets // [] | map(.source) | index("swedebeat_app")) | .key] == ["web"]' \
  || fail "swedebeat_app belongs to web only"
q '.services.web.environment.CLASSBEAT_SWEDEBEAT_ENABLED == "false"' \
  || fail "Swedebeat must be off by default"
q '.services.worker.environment.CLASSBEAT_SWEDEBEAT_ENABLED == "false"' \
  || fail "Swedebeat must be off by default on the worker too"
q '[.services.worker.secrets[].source] | index("database_maint_password") == null' \
  || fail "worker must not mount the maint password"
q '[.services | to_entries[] | select(.value.secrets // [] | map(.source) | index("postgres_password")) | .key] | sort == ["db","db-functions"]' \
  || fail "postgres_password belongs to db and db-functions only"
q '.services.web.environment.CLASSBEAT_TRUSTED_PROXIES == .services.caddy.networks.edge.ipv4_address' \
  || fail "the trusted proxy must be caddy's fixed address on edge"
q '[.services | to_entries[] | select(.value.environment // {} | has("CLASSBEAT_SMTP_CA_FILE")) | .key] == ["web"]'   || fail "CLASSBEAT_SMTP_CA_FILE belongs to web only (migrate would fail on an unmounted file)"
q '.services.web.environment.CLASSBEAT_SMTP_CA_FILE == "/run/smtp-ca/bridge.pem"'   || fail "web must get SMTP_CA_FILE"
q '.services.web.environment.CLASSBEAT_ENVIRONMENT == "prod"' || fail "web must run as prod"
q '[.services[] | select((.logging.options["max-size"] // null) == null)] | length == 0' \
  || fail "every service needs log rotation"

docker run --rm -e ACME_EMAIL -e CLASSBEAT_DOMAIN -v "$native/Caddyfile:/etc/caddy/Caddyfile:ro" \
  caddy:2.10.2 caddy validate --config /etc/caddy/Caddyfile >"$tmp/caddy.log" 2>&1 \
  || { cat "$tmp/caddy.log" >&2; fail "caddy validate failed"; }
echo "compose.prod.yaml and Caddyfile: ok"
