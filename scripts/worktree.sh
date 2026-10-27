#!/usr/bin/env bash
# Per-worktree dev stacks for classbeat.
#   worktree.sh init [issue]        .env and secrets/ for the current checkout (if missing)
#   worktree.sh new <issue> <slug>  worktree ../classbeat-wt/<issue>-<slug> from origin/main (or $WORKTREE_BASE_REF), then init
#   worktree.sh rm <issue>          stop the stack (volumes too), remove the worktree and its branch
#   worktree.sh ls                  worktrees with project name and ports
set -euo pipefail

SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
SECRETS=(postgres_password database_owner_password database_password)

die() { echo "worktree.sh: $*" >&2; exit 1; }

repo_root() { git rev-parse --show-toplevel; }

# Directory holding the worktrees: ../classbeat-wt of the main checkout.
wt_base() {
  local parent
  parent="$(dirname "$(repo_root)")"
  if [ "$(basename "$parent")" = "classbeat-wt" ]; then echo "$parent"; else echo "$parent/classbeat-wt"; fi
}

random_secret() { head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n'; }

cmd_init() {
  local issue="${1:-}" root
  root="$(repo_root)"
  cd "$root"
  if [ -n "$issue" ]; then
    case "$issue" in *[!0-9]*) die "issue must be a number" ;; esac
    [ "$issue" -le 9999 ] || die "issue number too large for the port scheme"
  fi

  if [ ! -f .env ]; then
    cp .env.example .env
    if [ -n "$issue" ]; then
      sed -i \
        -e "s/^COMPOSE_PROJECT_NAME=.*/COMPOSE_PROJECT_NAME=classbeat-$issue/" \
        -e "s/^WEB_PORT=.*/WEB_PORT=$((8000 + issue))/" \
        -e "s/^DB_PORT=.*/DB_PORT=$((55400 + issue))/" \
        -e "s/^MAILPIT_PORT=.*/MAILPIT_PORT=$((18000 + issue))/" .env
    fi
    echo "created .env"
  fi

  mkdir -p secrets
  chmod 700 secrets 2>/dev/null || true
  local name
  for name in "${SECRETS[@]}"; do
    if [ ! -f "secrets/$name" ]; then
      printf '%s' "$(random_secret)" > "secrets/$name"
      # The container user (uid 10001) must read the bind-mounted file; secrets/ itself is 0700.
      chmod 644 "secrets/$name" 2>/dev/null || true
      echo "created secrets/$name"
    fi
  done
}

cmd_new() {
  local issue="${1:-}" slug="${2:-}"
  [ -n "$issue" ] && [ -n "$slug" ] || die "usage: worktree.sh new <issue> <slug>"
  case "$issue" in *[!0-9]*) die "issue must be a number" ;; esac
  case "$slug" in *[!a-z0-9-]*) die "slug may only contain a-z, 0-9 and -" ;; esac
  local branch="$issue-$slug" dir
  dir="$(wt_base)/$branch"
  [ ! -e "$dir" ] || die "$dir already exists"
  mkdir -p "$(wt_base)"
  git fetch origin main --quiet
  if git show-ref --verify --quiet "refs/heads/$branch"; then
    git worktree add "$dir" "$branch"
  else
    git worktree add -b "$branch" "$dir" "${WORKTREE_BASE_REF:-origin/main}"
  fi
  (cd "$dir" && "$SELF" init "$issue")
  echo "worktree: $dir"
}

find_worktree() {
  local issue="$1" path
  while read -r path; do
    case "$(basename "$path")" in "$issue"-*) echo "$path"; return 0 ;; esac
  done < <(git worktree list --porcelain | sed -n 's/^worktree //p')
  return 1
}

cmd_rm() {
  local issue="${1:-}" dir branch
  [ -n "$issue" ] || die "usage: worktree.sh rm <issue>"
  case "$issue" in *[!0-9]*) die "issue must be a number" ;; esac
  dir="$(find_worktree "$issue")" || die "no worktree for issue $issue"
  branch="$(git -C "$dir" rev-parse --abbrev-ref HEAD)"
  if [ -f "$dir/compose.yaml" ]; then
    (cd "$dir" && docker compose down -v --remove-orphans) || echo "warning: docker compose down failed" >&2
  fi
  git worktree remove --force "$dir"
  if git branch -d "$branch" 2>/dev/null; then
    echo "deleted branch $branch"
  else
    echo "kept branch $branch (not merged)"
  fi
}

env_value() { sed -n "s/^$2=//p" "$1/.env" 2>/dev/null | head -n 1; }

cmd_ls() {
  local path
  printf '%-45s %-18s %-6s %-6s %s\n' WORKTREE PROJECT WEB DB MAILPIT
  while read -r path; do
    printf '%-45s %-18s %-6s %-6s %s\n' "$(basename "$path")" \
      "$(env_value "$path" COMPOSE_PROJECT_NAME)" "$(env_value "$path" WEB_PORT)" \
      "$(env_value "$path" DB_PORT)" "$(env_value "$path" MAILPIT_PORT)"
  done < <(git worktree list --porcelain | sed -n 's/^worktree //p')
}

case "${1:-}" in
  init) shift; cmd_init "$@" ;;
  new) shift; cmd_new "$@" ;;
  rm) shift; cmd_rm "$@" ;;
  ls) cmd_ls ;;
  *) die "usage: worktree.sh init [issue] | new <issue> <slug> | rm <issue> | ls" ;;
esac
