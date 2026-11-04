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
  "docs/runbooks/swag-classbeat.subdomain.conf swag-classbeat.subdomain.conf"
  "docs/runbooks/prod.env.example prod.env.example"
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
      [ -f "$root/$src" ] && [ ! -L "$root/$src" ] || { echo "release-assets: $src is not a regular file" >&2; exit 1; }
      cp "$root/$src" "$dir/$name"
    done
    chmod 0755 "$dir/check-prod-compose.sh"
    ( cd "$dir" && sha256sum -- * > SHA256SUMS )
    ;;
  *)
    echo "usage: release-assets.sh list | build DIR" >&2
    exit 2
    ;;
esac
