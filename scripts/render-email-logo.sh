#!/usr/bin/env bash
# Renders scripts/email-logo.svg (240x64 CSS px) to frontend/public/email/logo.png at 2x with a
# transparent background, using headless Chrome/Chromium. Run it only when the logo changes and
# commit the PNG; mail clients fetch /email/logo.png from the public base URL (ADR 0022 section 3).
set -euo pipefail
cd "$(dirname "$0")/.."
chrome="${CHROME:-$(command -v google-chrome || command -v chromium || command -v chromium-browser || true)}"
[ -n "$chrome" ] || { echo "set CHROME to a Chrome/Chromium binary" >&2; exit 1; }
mkdir -p frontend/public/email
"$chrome" --headless --no-sandbox --disable-gpu --hide-scrollbars \
  --default-background-color=00000000 --force-device-scale-factor=2 \
  --window-size=240,64 --screenshot="$PWD/frontend/public/email/logo.png" \
  "file://$PWD/scripts/email-logo.svg"
