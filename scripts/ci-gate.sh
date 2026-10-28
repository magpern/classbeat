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
set -euo pipefail

: "${NEEDS:?NEEDS is required}"
RESULTS="${RESULTS:-}"
echo "results: $RESULTS"

# An empty or short result list must not pass vacuously.
want="$(jq 'length' <<< "$NEEDS" | tr -d '\r')"
got="$(wc -w <<< "$RESULTS")"
[ "$want" -gt 0 ] && [ "$got" -eq "$want" ] || { echo "::error::expected $want results, got $got"; exit 1; }

# The `changes` output that gates a job; empty for jobs that always run.
filter_of() {
  case "$1" in
    backend) echo backend ;;
    frontend) echo frontend ;;
    e2e) echo stack ;;
    *) echo "" ;;
  esac
}

result_of() { jq -r --arg j "$1" '.[$j].result // "missing"' <<< "$NEEDS" | tr -d '\r'; }
filter_value() { jq -r --arg f "$1" '.changes.outputs[$f] // "missing"' <<< "$NEEDS" | tr -d '\r'; }

fail=0
for job in changes checks backend frontend e2e; do
  if [ "$(result_of "$job")" = missing ]; then
    echo "::error::job $job is not in needs"
    fail=1
  fi
done

while read -r job; do
  result="$(result_of "$job")"
  filter="$(filter_of "$job")"
  if [ "$result" = success ]; then
    continue
  elif [ "$result" = skipped ] && [ -n "$filter" ] && [ "$(filter_value "$filter")" = false ]; then
    echo "$job skipped: its filter ($filter) is false"
  elif [ -n "$filter" ]; then
    echo "::error::$job finished as $result (filter $filter is $(filter_value "$filter"))"
    fail=1
  else
    echo "::error::$job finished as $result"
    fail=1
  fi
done < <(jq -r 'keys[]' <<< "$NEEDS" | tr -d '\r')

exit "$fail"
