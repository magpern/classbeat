#!/usr/bin/env bash
# Installs classbeat's access-log rotation rule into swag's volume and dry-runs it (#826).
# The rule (swag-classbeat-logrotate.conf, a release asset beside this script) keeps the access log
# 30 days at most, as the privacy notice promises. This script schedules nothing: the daily run is
# the cron or systemd timer of runbook section 7 step 6, set up by hand. It reads no secret and
# touches only the one rule file inside the swag container.
#
#   ./install-swag-logrotate.sh              write the rule into swag, then `logrotate -d` (dry run)
#   ./install-swag-logrotate.sh --check      only the dry run, on the rule already installed
#   SWAG_CONTAINER=name                      the swag container (default `swag`)
#   RULE_FILE=path                           the rule to install (default: beside this script)
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
container="${SWAG_CONTAINER:-swag}"
dest=/config/log/classbeat-logrotate.conf
mode="${1:-install}"
case "$mode" in install|--check) ;; *) echo "usage: install-swag-logrotate.sh [--check]" >&2; exit 2 ;; esac

# The rule is a release asset beside this script; in a checkout it is under docs/runbooks.
rule="${RULE_FILE:-}"
if [ -z "$rule" ]; then
  for c in "$here/swag-classbeat-logrotate.conf" "$here/../docs/runbooks/swag-classbeat-logrotate.conf"; do
    if [ -f "$c" ]; then rule="$c"; break; fi
  done
fi

if [ "$mode" = install ]; then
  [ -f "$rule" ] || { echo "install-swag-logrotate: swag-classbeat-logrotate.conf not found (download every release asset, runbook section 3)" >&2; exit 1; }
  # Only the rule's text goes in, through stdin: no host path needs to be known or mounted.
  docker exec -i "$container" sh -c "cat > '$dest'" < "$rule"
  echo "installed $dest in container $container"
fi

# -d: debug, nothing is rotated or changed. A bad rule shows here, before the first real run.
docker exec "$container" logrotate -d "$dest"
echo "dry run ok. Schedule the real run daily (runbook section 7 step 6); nothing was scheduled here."
