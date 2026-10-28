#!/usr/bin/env bash
# Decides which CI jobs a change needs. Writes backend/frontend/stack/all (true|false) to
# $GITHUB_OUTPUT, or to stdout when that is unset (local use).
#
#   EVENT_NAME  github.event_name; anything but pull_request means "run everything"
#   BASE_SHA    pull request base commit    HEAD_SHA  pull request head commit
#
# Local try-out: EVENT_NAME=pull_request BASE_SHA=origin/main HEAD_SHA=HEAD scripts/ci-changes.sh
set -euo pipefail

: "${EVENT_NAME:?EVENT_NAME is required}"
out="${GITHUB_OUTPUT:-/dev/stdout}"

files=""
if [ "$EVENT_NAME" = pull_request ]; then
  : "${BASE_SHA:?BASE_SHA is required}" "${HEAD_SHA:?HEAD_SHA is required}"
  # Three dots: what the branch changed since it left the base, not what the base gained.
  files="$(git diff --name-only "${BASE_SHA}...${HEAD_SHA}")"
  echo "changed files:" >&2
  echo "  ${files//$'\n'/$'\n'  }" >&2
fi

# matches <extended regex>: true when any changed file matches.
matches() {
  if grep -Eq "$1" <<< "$files"; then echo true; else echo false; fi
}

if [ "$EVENT_NAME" != pull_request ] || [ "$(matches '^\.github/')" = true ]; then
  all=true
else
  all=false
fi

backend="$(matches '^(backend/|docker/postgres/)')"
frontend="$(matches '^frontend/')"
stack="$(matches '^((backend|frontend|e2e|docker)/|scripts/worktree\.sh$|Dockerfile$|compose[^/]*\.ya?ml$|\.dockerignore$)')"

if [ "$all" = true ]; then
  backend=true
  frontend=true
  stack=true
fi

{
  echo "backend=$backend"
  echo "frontend=$frontend"
  echo "stack=$stack"
  echo "all=$all"
} >> "$out"
