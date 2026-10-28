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
# Fails closed: any malformed input or failing jq call fails the gate.
set -euo pipefail

: "${NEEDS:?NEEDS is required}"
RESULTS="${RESULTS:-}"
echo "results: $RESULTS"

die() { echo "::error::$*"; exit 1; }

# NEEDS must be an object of objects.
jq -e 'type == "object" and all(.[]; type == "object")' <<< "$NEEDS" > /dev/null 2>&1 \
  || die "NEEDS is not a JSON object of jobs"

# An empty or short result list must not pass vacuously.
want="$(jq 'length' <<< "$NEEDS" | tr -d '\r')"
got="$(wc -w <<< "$RESULTS")"
[ "$want" -gt 0 ] && [ "$got" -eq "$want" ] || die "expected $want results, got $got"

# The `changes` output that gates a job; empty for jobs that always run.
filter_of() {
  case "$1" in
    backend) echo backend ;;
    frontend) echo frontend ;;
    e2e) echo stack ;;
    *) echo "" ;;
  esac
}

# One jq call, one line per job: job, result, and the three filter outputs (backend, frontend,
# stack). Absent values are "missing", empty ones "empty". A failing jq fails the gate: this is a
# plain assignment, so set -e sees its exit status.
table="$(jq -r '
  def v: if . == null then "missing" elif . == "" then "empty" else tostring end;
  (.changes.outputs // {}) as $o
  | to_entries[]
  | [.key, (.value.result | v), ($o.backend | v), ($o.frontend | v), ($o.stack | v)]
  | join(" ")' <<< "$NEEDS" | tr -d '\r')"

fail=0
for job in changes checks backend frontend e2e; do
  if ! grep -q "^$job " <<< "$table"; then
    echo "::error::job $job is not in needs"
    fail=1
  fi
done

while read -r job result fb ff fs; do
  [ "$result" != success ] || continue
  filter="$(filter_of "$job")"
  case "$filter" in
    backend) value="$fb" ;;
    frontend) value="$ff" ;;
    stack) value="$fs" ;;
    *) value="" ;;
  esac
  if [ -n "$filter" ] && [ "$result" = skipped ] && [ "$value" = false ]; then
    echo "$job skipped: its filter ($filter) is false"
  else
    echo "::error::$job finished as $result${filter:+ (filter $filter is $value)}"
    fail=1
  fi
done <<< "$table"

exit "$fail"
