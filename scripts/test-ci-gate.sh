#!/usr/bin/env bash
# Tests scripts/ci-gate.sh, scripts/ci-changes.sh and scripts/ci-shards.sh with sample inputs.
# Run by the `checks` job.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"

failures=0
report() { # report <ok|FAIL> <message>
  echo "$1 $2"
  [ "$1" = ok ] || failures=$((failures + 1))
}

# needs <changes> <checks> <backend> <frontend> <e2e> <backend filter> <frontend filter> <stack filter>
#       [backend-tests]
# backend-tests (the pytest shard matrix, one aggregate result) defaults to the backend result.
needs() {
  jq -nc --arg ch "$1" --arg ck "$2" --arg b "$3" --arg f "$4" --arg e "$5" \
    --arg fb "$6" --arg ff "$7" --arg fs "$8" --arg bt "${9:-$3}" '{
      changes: {result: $ch, outputs: {backend: $fb, frontend: $ff, stack: $fs, all: "false"}},
      checks: {result: $ck, outputs: {}},
      backend: {result: $b, outputs: {}},
      "backend-tests": {result: $bt, outputs: {}},
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
gate 'NEEDS is a string'                      1 '"abcdef"' "a b c d e f"
gate 'NEEDS is a number'                      1 '6' "a b c d e f"
gate 'NEEDS is an array'                      1 '[]' ""
gate 'NEEDS is null'                          1 'null' ""
gate 'NEEDS is an object of strings'          1 '{"a":"x","b":"x","c":"x","d":"x","e":"x","f":"x"}' "a b c d e f"
gate 'NEEDS is malformed JSON'                1 '{' "a"
# Value-level mutations of a valid docs-only input: each must fail the gate. The results list
# is passed as six words so the count check cannot be what rejects them.
docs="$(needs success success skipped skipped skipped false false false)"
five="success success skipped skipped skipped skipped"
mutated() { jq -c "$1" <<< "$docs"; }
gate "filter is boolean false"                1 "$(mutated '.changes.outputs.backend = false')" "$five"
gate "filter is number 0"                     1 "$(mutated '.changes.outputs.backend = 0')" "$five"
gate "filter is False"                        1 "$(mutated '.changes.outputs.backend = "False"')" "$five"
gate "filter has a trailing space"            1 "$(mutated '.changes.outputs.stack = "false "')" "$five"
gate "filter is false then a fake job line"   1 "$(mutated '.changes.outputs.stack = "false\nx success"')" "$five"
gate "filter is two words"                    1 "$(mutated '.changes.outputs.backend = "false true"')" "$five"
gate "filter is an array"                     1 "$(mutated '.changes.outputs.backend = ["false"]')" "$five"
gate "outputs is a string"                    1 "$(mutated '.changes.outputs = "x"')" "$five"
gate "outputs is missing"                     1 "$(mutated 'del(.changes.outputs)')" "$five"
gate "result is null"                         1 "$(mutated '.checks.result = null')" "$five"
gate "result is missing"                      1 "$(mutated 'del(.checks.result)')" "$five"
gate "result is Success"                      1 "$(mutated '.checks.result = "Success"')" "$five"
gate "result is a boolean"                    1 "$(mutated '.checks.result = true')" "$five"
gate "result is success x"                    1 "$(mutated '.checks.result = "success x"')" "$five"
gate "backend missing, crafted changes result" 1 "$(mutated 'del(.backend) | .changes.result = "success\nbackend success"')" "$five"
gate "job value is a string"                  1 "$(mutated '.checks = "success"')" "$five"
gate "job value is null"                      1 "$(mutated '.checks = null')" "$five"
gate "job value is an array"                  1 "$(mutated '.checks = []')" "$five"
gate "extra job"                              1 "$(mutated '.extra = {"result": "success", "outputs": {}}')" "$five success"
gate "renamed job E2E"                        1 "$(mutated 'del(.e2e) | .E2E = {"result": "success"}')" "$five"
gate "job name with a space"                  1 "$(mutated 'del(.backend) | .["backend x"] = {"result": "success"}')" "$five"
gate "job name with a newline"                1 "$(mutated 'del(.backend) | .["zz\nbackend"] = {"result": "success"}')" "$five"
gate "two JSON documents"                     1 "$docs $docs" "$five"
gate "NEEDS is whitespace"                    1 "   " "$five"
gate "valid input still passes"               0 "$docs" "$five"
gate "result count mismatch"                 1 "$(needs success success skipped skipped skipped false false false)" "success success"
gate "five results for six jobs"              1 "$docs" "success success skipped skipped skipped"

# The backend-tests matrix (the pytest shards): one aggregate result, gated by the backend filter.
#     name                                  want  needs: ... filters, then the backend-tests result
gate "shards passed"                          0 "$(needs success success success skipped skipped true false false success)"
gate "a shard failed"                         1 "$(needs success success success skipped skipped true false false failure)"
gate "a shard cancelled"                      1 "$(needs success success success skipped skipped true false false cancelled)"
gate "shards skipped, backend filter true"    1 "$(needs success success success skipped skipped true false false skipped)"
gate "shards skipped, backend filter false"   0 "$(needs success success skipped skipped skipped false false false skipped)"
gate "static failed, shards passed"           1 "$(needs success success failure skipped skipped true false false success)"
gate "shards failed, backend filter false"    1 "$(needs success success skipped skipped skipped false false false failure)"
gate "shards missing, six results"            1 "$(needs success success success skipped skipped true false false | jq -c 'del(.["backend-tests"])')" "success success success success skipped skipped"
gate "shards missing from needs"              1 "$(needs success success success skipped skipped true false false | jq -c 'del(.["backend-tests"])')" "success success success skipped skipped"
gate "a shard listed as its own job"          1 "$(needs success success success skipped skipped true false false | jq -c '.["backend-tests-1"] = {"result": "success"}')" "success success success success skipped skipped success"

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
changes "backup script"     pull_request      "scripts/backup.sh"                   "false false true false"
changes "restore script"    pull_request      "scripts/restore.sh"                  "false false true false"
changes "drill script"      pull_request      "scripts/backup-restore-drill.sh"     "false false true false"
changes "validate script"   pull_request      "scripts/validate-backup.sh"          "false false true false"
changes "drill probe"       pull_request      "scripts/drill/probe.py"              "false false true false"
changes "ci-gate script"    pull_request      "scripts/ci-gate.sh"                  "true true true true"
changes "ci-changes script" pull_request      "scripts/ci-changes.sh"               "true true true true"
changes "ci-shards script"  pull_request      "scripts/ci-shards.sh"                "true true true true"
changes "test script"       pull_request      "scripts/test-ci-gate.sh"             "true true true true"
changes ".gitattributes"    pull_request      ".gitattributes"                      "true true true true"
changes "unrelated script"  pull_request      "scripts/other.sh"                    "false false false false"
changes ".env.example"      pull_request      ".env.example"                        "false false true false"
changes ".trivyignore"      pull_request      ".trivyignore"                        "false false true false"
changes ".github changes"   pull_request      ".github/workflows/ci.yml"            "true true true true"
changes "schedule"          schedule          "README.md"                           "true true true true"
changes "manual dispatch"   workflow_dispatch "README.md"                           "true true true true"
# A merge group diffs its base_sha (the target branch tip) against its head_sha like a pull request.
changes "merge group docs"  merge_group       "README.md docs/x.md"                 "false false false false"
changes "merge group back"  merge_group       "backend/app/x.py"                    "true false true false"
changes "merge group front" merge_group       "frontend/src/a.ts"                   "false true true false"
changes "merge group ci"    merge_group       ".github/workflows/ci.yml"            "true true true true"
changes "unknown event"     push              "README.md"                           "true true true true"


# Paths that need care: a command-line list of files per case, created and committed as given.
# changes_git <name> <expected> <setup function>: the function runs in the scratch repo on top of
# a base commit that holds backend/app/x.py and docs/y.md.
changes_git() {
  local name="$1" want="$2" setup="$3" head out
  git -C "$tmp" reset -q --hard "$base"
  git -C "$tmp" clean -qfd
  mkdir -p "$tmp/backend/app" "$tmp/docs"
  echo base > "$tmp/backend/app/x.py"
  echo base > "$tmp/docs/y.md"
  git -C "$tmp" add -A
  git -C "$tmp" commit -q -m seed
  local seed
  seed="$(git -C "$tmp" rev-parse HEAD)"
  (cd "$tmp" && "$setup")
  git -C "$tmp" add -A
  git -C "$tmp" commit -q --allow-empty -m change
  head="$(git -C "$tmp" rev-parse HEAD)"
  out="$(cd "$tmp" && env -u GITHUB_OUTPUT EVENT_NAME=pull_request BASE_SHA="$seed" HEAD_SHA="$head" \
    "$here/ci-changes.sh" 2> /dev/null | cut -d= -f2 | tr '\n' ' ')"
  if [ "$out" = "$want " ]; then report ok "changes: $name"; else report FAIL "changes: $name (got '$out', want '$want')"; fi
}
mv_out_of_backend() { git mv backend/app/x.py docs/x.py; }
mv_backend_to_frontend() { mkdir -p frontend/src && git mv backend/app/x.py frontend/src/x.py; }
add_non_ascii() { echo a > "backend/app/$(printf '\303\245').py"; }
add_quote_name() { echo a > 'backend/app/q"x.py'; }
add_newline_name() { echo a > "backend/app/a"$'\n'"b.py"; }
add_backslash_name() { mkdir -p frontend && echo a > 'frontend/a\b.ts'; }
add_docs_non_ascii() { echo a > "docs/$(printf '\303\245').md"; }
changes_git "move out of backend/"          "true false true false" mv_out_of_backend
changes_git "move backend/ to frontend/"    "true true true false"  mv_backend_to_frontend
changes_git "non-ASCII name in backend/"    "true false true false" add_non_ascii
changes_git "double quote in backend/ name" "true false true false" add_quote_name
changes_git "newline in backend/ name"      "true false true false" add_newline_name
# A backslash is a path separator on Windows; the case only makes sense elsewhere.
case "$(uname -s)" in
  MINGW* | MSYS* | CYGWIN*) ;;
  *) changes_git "backslash in frontend/ name"   "false true true false" add_backslash_name ;;
esac
changes_git "non-ASCII name in docs/"       "false false false false" add_docs_non_ascii

# An unresolvable base runs everything instead of failing.
out="$(cd "$tmp" && env -u GITHUB_OUTPUT EVENT_NAME=pull_request BASE_SHA=0000000000000000000000000000000000000001   HEAD_SHA=HEAD "$here/ci-changes.sh" 2> /dev/null | cut -d= -f2 | tr '\n' ' ')"
if [ "$out" = "true true true true " ]; then report ok "changes: unknown base runs everything"; else report FAIL "changes: unknown base (got '$out')"; fi

# The same for a merge group, and for a merge group (or pull request) without its SHAs: the
# diff cannot be computed, so everything runs (fail closed), never "nothing changed".
fail_closed() { # fail_closed <name> <env assignments...>
  local name="$1" out
  shift
  out="$(cd "$tmp" && env -u GITHUB_OUTPUT -u BASE_SHA -u HEAD_SHA "$@" "$here/ci-changes.sh" 2> /dev/null | cut -d= -f2 | tr '\n' ' ')"
  if [ "$out" = "true true true true " ]; then report ok "changes: $name"; else report FAIL "changes: $name (got '$out')"; fi
}
fail_closed "merge group, unknown base runs everything" EVENT_NAME=merge_group BASE_SHA=0000000000000000000000000000000000000001 HEAD_SHA=HEAD
fail_closed "merge group, unknown head runs everything" EVENT_NAME=merge_group BASE_SHA=HEAD HEAD_SHA=0000000000000000000000000000000000000002
fail_closed "merge group, no base runs everything"      EVENT_NAME=merge_group HEAD_SHA=HEAD
fail_closed "merge group, empty SHAs run everything"    EVENT_NAME=merge_group BASE_SHA= HEAD_SHA=
fail_closed "pull request, no head runs everything"     EVENT_NAME=pull_request BASE_SHA=HEAD

# ci-shards.sh: the shard count is the length of the backend-tests `shard` list, and only a
# matrix that is exactly `shard: [1..N]` has one. Needs yq (on the CI runners; skipped without).
# shards <name> <expected count, or "fail"> <matrix as YAML flow>
shards() {
  local name="$1" want="$2" matrix="$3" got
  printf 'jobs:\n  backend-tests:\n    strategy:\n      matrix: %s\n' "$matrix" > "$tmp/wf.yml"
  if got="$("$here/ci-shards.sh" "$tmp/wf.yml" 2> /dev/null)"; then :; else got=fail; fi
  if [ "$got" = "$want" ]; then report ok "shards: $name"; else report FAIL "shards: $name (got '$got', want '$want')"; fi
}
if command -v yq > /dev/null; then
  shards "three shards"                3    '{shard: [1, 2, 3]}'
  shards "one shard"                   1    '{shard: [1]}'
  shards "five shards"                 5    '{shard: [1, 2, 3, 4, 5]}'
  shards "a second axis"               fail '{shard: [1, 2, 3], python: ["3.12"]}'
  shards "an include"                  fail '{shard: [1, 2, 3], include: [{shard: 4}]}'
  shards "an exclude"                  fail '{shard: [1, 2, 3], exclude: [{shard: 3}]}'
  shards "a gap"                       fail '{shard: [1, 3]}'
  shards "a repeat"                    fail '{shard: [1, 1, 2]}'
  shards "out of order"                fail '{shard: [2, 1, 3]}'
  shards "starts at 0"                 fail '{shard: [0, 1, 2]}'
  shards "strings"                     fail '{shard: ["1", "2"]}'
  shards "empty list"                  fail '{shard: []}'
  shards "not a list"                  fail '{shard: 3}'
  shards "no shard axis"               fail '{part: [1, 2]}'
  # shellcheck disable=SC2016  # a literal GitHub expression, not shell
  shards "matrix from an expression"   fail '"${{ fromJSON(needs.x.outputs.m) }}"'
  rm -f "$tmp/wf.yml"
  if "$here/ci-shards.sh" "$tmp/missing.yml" > /dev/null 2>&1; then report FAIL "shards: missing file"; else report ok "shards: missing file"; fi
  # The real workflow has a valid matrix.
  if n="$("$here/ci-shards.sh" "$here/../.github/workflows/ci.yml" 2> /dev/null)"; then
    report ok "shards: ci.yml has $n shards"
  else
    report FAIL "shards: ci.yml has no valid backend-tests matrix"
  fi
else
  # On the CI runner yq is required: these cases must not skip silently there.
  if [ -n "${CI:-}" ]; then report FAIL "shards: yq is missing on the CI runner"; else echo "skip shards: no yq here (CI has it)"; fi
fi

[ "$failures" -eq 0 ] || { echo "$failures test(s) failed"; exit 1; }
echo "all tests passed"
