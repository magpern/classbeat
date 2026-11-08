#!/usr/bin/env bash
# The files a GitHub Release carries for an image-only install (ADR 0020, #566): the one list the
# release workflow and CI both read. A server needs these, the two images and its own .env and
# secrets/, never a clone.
#
#   scripts/release-assets.sh list          one "<source path>  <asset name>" line per asset
#   scripts/release-assets.sh build DIR     copy the assets into a new, empty DIR and write DIR/SHA256SUMS
#
# Assets are repository files copied byte for byte from the tagged checkout: nothing is generated,
# stamped or edited, so the file on the server is the file CI checked at that tag. None can hold a
# secret (all are committed, reviewed files; secrets/ and .env are git-ignored, and the Swedebeat
# app key only ever exists inside the app image). SHA256SUMS lists the others by name.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

assets=(
  "compose.prod.yaml compose.prod.yaml"
  "compose.prod.loopback.yaml compose.prod.loopback.yaml"
  "compose.prod.lan.yaml compose.prod.lan.yaml"
  "scripts/check-prod-compose.sh check-prod-compose.sh"
  "scripts/check-prod-compose-rollback.bash check-prod-compose-rollback.bash"
  "scripts/check-prod-compose-setup.bash check-prod-compose-setup.bash"
  "scripts/check-prod-compose-rules.bash check-prod-compose-rules.bash"
  "scripts/check-prod-compose-secrets.bash check-prod-compose-secrets.bash"
  "scripts/check-prod-compose-selftest-compose.bash check-prod-compose-selftest-compose.bash"
  "scripts/check-prod-compose-selftest-yaml.bash check-prod-compose-selftest-yaml.bash"
  "scripts/check-prod-compose-owner.bash check-prod-compose-owner.bash"
  "scripts/check-prod-compose-secretfiles.bash check-prod-compose-secretfiles.bash"
  "scripts/check-prod-compose-turnstile.bash check-prod-compose-turnstile.bash"
  "scripts/check-prod-compose-runbook.bash check-prod-compose-runbook.bash"
  "scripts/check-prod-compose-site.bash check-prod-compose-site.bash"
  "scripts/check-prod-compose-site-selftest.bash check-prod-compose-site-selftest.bash"
  "scripts/check-prod-compose-probe.bash check-prod-compose-probe.bash"
  "docs/runbooks/swag-classbeat.subdomain.conf swag-classbeat.subdomain.conf"
  "docs/runbooks/prod.env.example prod.env.example"
  "scripts/backup.sh backup.sh"
  "scripts/restore.sh restore.sh"
  "scripts/validate-backup.sh validate-backup.sh"
)

case "${1:-}" in
  list)
    printf '%s\n' "${assets[@]}"
    ;;
  build)
    dir="${2:?usage: release-assets.sh build DIR}"
    mkdir -p "$dir"
    [ -z "$(ls -A "$dir")" ] || { echo "release-assets: $dir is not empty" >&2; exit 1; }
    for a in "${assets[@]}"; do
      src="${a% *}"; name="${a#* }"
      # `sha256sum -- *` and the release's `$assets/*` glob skip dotfiles: a hidden asset (a .env, say)
      # would be uploaded by neither or checksummed by neither, so its name may not start with a dot.
      [[ "$name" != .* && "$name" != */* ]] || { echo "release-assets: asset name '$name' must not start with a dot or hold a slash" >&2; exit 1; }
      [ -f "$root/$src" ] && [ ! -L "$root/$src" ] || { echo "release-assets: $src is not a regular file" >&2; exit 1; }
      cp "$root/$src" "$dir/$name"
    done
    chmod 0755 "$dir/check-prod-compose.sh" "$dir/backup.sh" "$dir/restore.sh" "$dir/validate-backup.sh"
    ( cd "$dir" && sha256sum -- * > SHA256SUMS )
    ;;
  *)
    echo "usage: release-assets.sh list | build DIR" >&2
    exit 2
    ;;
esac
