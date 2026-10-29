#!/usr/bin/env bash
# CI (checks job): the `with.version` of every setup-uv step in any workflow or composite action
# must equal the Dockerfile's uv tag; Dependabot only bumps the Dockerfile. A missing or computed
# (`${{ }}`) version fails, and so does finding no setup-uv step at all. Needs mikefarah yq v4.
# Run from the repository root. Moved out of ci.yml to keep it under 500 lines (#116 item 7).
set -euo pipefail
yqv="$(yq --version)"
echo "$yqv"
grep -Eq 'mikefarah/yq.* v4\.' <<< "$yqv" || { echo "::error::need mikefarah yq v4"; exit 1; }
want="$(sed -n 's|^FROM ghcr.io/astral-sh/uv:\([0-9][0-9.]*\) .*|\1|p' Dockerfile | tr -d '\r')"
[ -n "$want" ] || { echo "::error::no uv tag found in the Dockerfile"; exit 1; }
query="$(cat <<'Q'
[(.jobs // {} | to_entries | .[] | .key as $j | .value.steps[]? | [$j, .]),
 (.runs.steps[]? | ["(composite action)", .])]
| .[] | select(((.[1].uses // "") | downcase) | test("^astral-sh/setup-uv@"))
| .[0] + "\t" + (.[1].name // .[1].uses) + "\t" + ((.[1].with.version | select(tag == "!!str")) // "NONE") + "\t" + ((.[1].with // {} | has("version-file")) | tostring)
Q
)"
shopt -s nullglob globstar
files=(.github/workflows/*.yml .github/workflows/*.yaml .github/actions/**/action.yml .github/actions/**/action.yaml)
found=0
bad=0
for f in "${files[@]}"; do
  rows="$(yq -r "$query" "$f")"
  [ -n "$rows" ] || continue
  while IFS=$'\t' read -r job step v vfile; do
    vfile="${vfile%$'\r'}"
    found=$((found + 1))
    if [ "$vfile" = true ]; then
      echo "::error file=$f::job '$job', step '$step': setup-uv must not set version-file; use a literal version"
      bad=1
    elif [ "$v" != "$want" ]; then
      echo "::error file=$f::job '$job', step '$step': setup-uv version '$v' is not the Dockerfile uv tag $want (a literal string is required)"
      bad=1
    fi
  done <<< "$rows"
done
[ "$found" -gt 0 ] || { echo "::error::no setup-uv step found"; exit 1; }
exit "$bad"
