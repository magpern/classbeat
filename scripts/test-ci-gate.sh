#!/usr/bin/env bash
# Tests scripts/ci-gate.sh and scripts/ci-changes.sh with sample inputs. Run by the `checks` job.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"

failures=0
report() { # report <ok|FAIL> <message>
  echo "$1 $2"
  [ "$1" = ok ] || failures=$((failures + 1))
}

# needs <changes> <checks> <backend> <frontend> <e2e> <backend filter> <frontend filter> <stack filter>
needs() {
  jq -nc --arg ch "$1" --arg ck "$2" --arg b "$3" --arg f "$4" --arg e "$5" \
    --arg fb "$6" --arg ff "$7" --arg fs "$8" '{
      changes: {result: $ch, outputs: {backend: $fb, frontend: $ff, stack: $fs, all: "false"}},
      checks: {result: $ck, outputs: {}},
      backend: {result: $b, outputs: {}},
      frontend: {result: $f, outputs: {}},
      e2e: {result: $e, outputs: {}}}'
}

# gate <name> <expected exit 0|1> <needs json> [results override]
gate() {
  local name="$1" want="$2" json="$3" results got=0
  results="${4-$(jq -r '[.[].result] | join(" ")' <<< "$json")}"
  NEEDS="$json" RESULTS="$results" "$here/ci-gate.sh" > /dev/null 2>&1 || got=$?
  if [ "$got" -eq "$want" ]; then report ok "gate: $name"; else report FAIL "gate: $name (exit $got, want $want)"; fi
}

#     name                                  want  needs: changes checks backend frontend e2e  filters: backend frontend stack
gate "docs only, all skipped"                 0 "$(needs success success skipped skipped skipped false false false)"
gate "everything ran and passed"              0 "$(needs success success success success success true true true)"
gate "backend only, others skipped"           0 "$(needs success success success skipped skipped true false false)"
gate "ran although its filter was false"      0 "$(needs success success success success success false false false)"
gate "backend skipped, filter true"           1 "$(needs success success skipped skipped skipped true false false)"
gate "frontend skipped, filter true"          1 "$(needs success success skipped skipped skipped false true false)"
gate "e2e skipped, stack filter true"         1 "$(needs success success skipped skipped skipped false false true)"
gate "backend failed"                         1 "$(needs success success failure skipped skipped true false false)"
gate "backend cancelled"                      1 "$(needs success success cancelled skipped skipped true false false)"
gate "failed although its filter was false"   1 "$(needs success success failure skipped skipped false false false)"
gate "checks failed"                          1 "$(needs success failure skipped skipped skipped false false false)"
gate "checks skipped"                         1 "$(needs success skipped skipped skipped skipped false false false)"
gate "changes failed"                         1 "$(needs failure success skipped skipped skipped '' '' '')"
gate "changes skipped"                        1 "$(needs skipped success skipped skipped skipped '' '' '')"
gate "filter output missing"                  1 "$(needs success success skipped skipped skipped '' '' '')"
gate "job missing from needs"                 1 "$(needs success success skipped skipped skipped false false false | jq -c 'del(.e2e)')"
gate "empty needs"                            1 "{}"
gate "result count mismatch"                  1 "$(needs success success skipped skipped skipped false false false)" "success success"

# changes <name> <event> <"file file ..."> <expected "backend frontend stack all">
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
git -C "$tmp" init -q
git -C "$tmp" config user.email t@example.com
git -C "$tmp" config user.name t
git -C "$tmp" commit -q --allow-empty -m base
base="$(git -C "$tmp" rev-parse HEAD)"
changes() {
  local name="$1" event="$2" files="$3" want="$4" f head out
  git -C "$tmp" reset -q --hard "$base"
  for f in $files; do
    mkdir -p "$tmp/$(dirname "$f")"
    echo "$name" > "$tmp/$f"
  done
  git -C "$tmp" add -A
  git -C "$tmp" commit -q --allow-empty -m change
  head="$(git -C "$tmp" rev-parse HEAD)"
  out="$(cd "$tmp" && env -u GITHUB_OUTPUT EVENT_NAME="$event" BASE_SHA="$base" HEAD_SHA="$head" "$here/ci-changes.sh" 2> /dev/null \
    | cut -d= -f2 | tr '\n' ' ')"
  if [ "$out" = "$want " ]; then report ok "changes: $name"; else report FAIL "changes: $name (got '$out', want '$want')"; fi
}

#       name                event             files                                 backend frontend stack all
changes "docs only"         pull_request      "README.md docs/x.md AGENTS.md"       "false false false false"
changes "backend only"      pull_request      "backend/app/x.py"                    "true false true false"
changes "frontend only"     pull_request      "frontend/src/a.ts"                   "false true true false"
changes "e2e only"          pull_request      "e2e/tests/a.ts"                      "false false true false"
changes "Dockerfile"        pull_request      "Dockerfile"                          "false false true false"
changes "compose file"      pull_request      "compose.e2e.yaml"                    "false false true false"
changes "docker files"      pull_request      "docker/nginx/x.conf"                 "false false true false"
changes "db init script"    pull_request      "docker/postgres/init/10-roles.sql"   "true false true false"
changes "worktree script"   pull_request      "scripts/worktree.sh"                 "false false true false"
changes "other script"      pull_request      "scripts/ci-gate.sh"                  "false false false false"
changes ".github changes"   pull_request      ".github/workflows/ci.yml"            "true true true true"
changes "schedule"          schedule          "README.md"                           "true true true true"
changes "manual dispatch"   workflow_dispatch "README.md"                           "true true true true"

[ "$failures" -eq 0 ] || { echo "$failures test(s) failed"; exit 1; }
echo "all tests passed"
