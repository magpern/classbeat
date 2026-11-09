#!/usr/bin/env bash
# The one source of a CI shard count, per job. Prints N, the number of shards (or, with `--list`,
# the JSON list [1, 2, ..., N]), after checking the job's matrix:
#
#   backend-tests (default)  the matrix is exactly `shard: [1, 2, ..., N]`: no other axis, no include
#                            or exclude, no repeated or missing shard. N is the length of the list.
#   e2e                      the matrix is exactly `shard: ${{ fromJSON(needs.changes.outputs.e2e_matrix) }}`
#                            (no other axis, no include or exclude), and N is the positive integer
#                            `env.E2E_SHARD_COUNT` of the e2e job. The list is dynamic so that `changes`
#                            can give a stack-only change `[1]` instead of N runners with nothing to do.
#
# `changes` computes the counts and lists; the shard checks and every shard take them from its
# outputs, so they can't disagree. Never strategy.job-total. Tested by scripts/test-ci-gate.sh.
#
#   scripts/ci-shards.sh [workflow file [job [--list]]]   # default .github/workflows/ci.yml backend-tests
set -euo pipefail

workflow="${1:-.github/workflows/ci.yml}"
job="${2:-backend-tests}"
mode="${3:-count}"
die() { echo "::error::$*" >&2; exit 1; }

[ -f "$workflow" ] || die "no workflow file $workflow"
case "$mode" in count | --list) ;; *) die "unknown option $mode" ;; esac

case "$job" in
  backend-tests)
    matrix="$(yq -o=json -I=0 '.jobs["backend-tests"].strategy.matrix' "$workflow")" \
      || die "could not read the backend-tests matrix"
    # shellcheck disable=SC2016  # jq program, not shell
    total="$(jq -r '
      if type != "object" then error("the backend-tests matrix must be a mapping") else . end
      | if keys != ["shard"] then error("the backend-tests matrix must have the one axis shard, and no include or exclude") else . end
      | .shard
      | if type != "array" or length == 0 then error("shard must be a non-empty list") else . end
      | if . != [range(1; length + 1)] then error("shard must be the list 1 to N") else length end
    ' <<< "$matrix" 2>&1)" || die "${total#jq: error (at <stdin>:*): }"
    ;;
  e2e)
    matrix="$(yq -o=json -I=0 '.jobs.e2e.strategy.matrix' "$workflow")" \
      || die "could not read the e2e matrix"
    count="$(yq -o=json -I=0 '.jobs.e2e.env.E2E_SHARD_COUNT' "$workflow")" \
      || die "could not read the e2e shard count"
    # shellcheck disable=SC2016  # jq program, not shell
    total="$(jq -rn --argjson matrix "$matrix" --argjson count "$count" '
      "${{ fromJSON(needs.changes.outputs.e2e_matrix) }}" as $expr
      | $matrix
      | if type != "object" then error("the e2e matrix must be a mapping") else . end
      | if keys != ["shard"] then error("the e2e matrix must have the one axis shard, and no include or exclude") else . end
      | if .shard != $expr then error("the e2e shard axis must be exactly " + $expr) else . end
      | $count
      | if type != "number" or . != floor or . < 1 or . > 20 then error("env.E2E_SHARD_COUNT of the e2e job must be an integer from 1 to 20") else . end
    ' 2>&1)" || die "${total#jq: error (at <unknown>:*): }"
    ;;
  *) die "unknown job $job (backend-tests or e2e)" ;;
esac

[[ $total =~ ^[1-9][0-9]*$ ]] || die "bad shard count: $total"
if [ "$mode" = --list ]; then
  jq -nc --argjson n "$total" '[range(1; $n + 1)]'
else
  echo "$total"
fi
