#!/usr/bin/env bash
# The decision of the `ci` gate job (the one required check), kept here so it can be tested
# (scripts/test-ci-gate.sh).
#
#   NEEDS    toJSON(needs) of the gate job
#   RESULTS  join(needs.*.result, ' ')
#
# `changes` and `checks` must have succeeded. Every other job must have succeeded, or have
# been skipped while the `changes` output that gates it is "false". A job that is skipped,
# cancelled or failed while its filter is true fails the gate, and so does a missing job.
#
# Fails closed. jq validates the values, not only the shape, before it decides anything: exactly
# one JSON document; exactly the five expected jobs; every result one of success, failure,
# cancelled or skipped; every filter output the string "true" or "false". Only then does it
# emit one verdict line per job, built from those validated values. Bash never splits or
# interprets the raw input.
set -euo pipefail

: "${NEEDS:?NEEDS is required}"
RESULTS="${RESULTS:-}"
echo "results: $RESULTS"

die() { echo "::error::$*"; exit 1; }

# shellcheck disable=SC2016  # jq program, not shell
program='
  def fail($m): error($m);
  [inputs] as $in
  | if ($in | length) != 1 then fail("NEEDS must be exactly one JSON document") else $in[0] end
  | . as $n
  | if type != "object" then fail("NEEDS must be a JSON object") else . end
  | if (keys | map(select(test("^[A-Za-z0-9_-]+$") | not)) | length) > 0
      then fail("a job name has unexpected characters") else . end
  | if keys != ["backend", "changes", "checks", "e2e", "frontend"]
      then fail("NEEDS must hold exactly the jobs changes, checks, backend, frontend, e2e") else . end
  | if all(.[]; type == "object"
        and (.result | type) == "string"
        and (.result | IN("success", "failure", "cancelled", "skipped")))
      then . else fail("every job needs a result of success, failure, cancelled or skipped") end
  | if (.changes.outputs | type) != "object" then fail("changes has no outputs object") else . end
  | if (["backend", "frontend", "stack"]
        | all(. as $f | $n.changes.outputs[$f] | type == "string" and IN("true", "false")))
      then . else fail("every filter output must be the string true or false") end
  | {backend: "backend", frontend: "frontend", e2e: "stack"} as $filter
  | to_entries[]
  | .key as $j
  | .value.result as $r
  | if $r == "success" then "ok \($j) succeeded"
    elif $j == "changes" or $j == "checks" then "err \($j) finished as \($r)"
    elif $r == "skipped" and $n.changes.outputs[$filter[$j]] == "false"
      then "ok \($j) skipped: its filter (\($filter[$j])) is false"
    else "err \($j) finished as \($r) (filter \($filter[$j]) is \($n.changes.outputs[$filter[$j]]))"
    end'

# A failing or erroring jq fails the gate (plain assignment, so set -e sees its exit status).
if ! verdict="$(jq -r -n "$program" <<< "$NEEDS" 2>&1)"; then
  echo "$verdict" | tr -d '\r'
  die "NEEDS is malformed"
fi
verdict="$(tr -d '\r' <<< "$verdict")"

# An empty or short result list must not pass vacuously.
got="$(wc -w <<< "$RESULTS")"
[ "$got" -eq 5 ] || die "expected 5 results, got $got"

fail=0
while IFS= read -r line; do
  case "$line" in
    "ok "*) echo "${line#ok }" ;;
    "err "*) echo "::error::${line#err }"; fail=1 ;;
    *) echo "::error::unexpected verdict line"; fail=1 ;;
  esac
done <<< "$verdict"

exit "$fail"
