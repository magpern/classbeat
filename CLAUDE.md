@AGENTS.md

## Before any `gh pr merge` (read this first)

- No `Agent review (<model>): PASS <sha>` comment for the PR's **current head commit** → **do
  not merge.** Run the review: spawn a fresh agent for the PR's first review and **resume that
  same agent** (SendMessage to its agent id, with the new head) for every later round on the
  PR, security-critical PRs too; a fresh one only after a second `FAIL` or when it cannot be
  resumed (the comment says so) (the `reviewer` agent in `.claude/agents/`;
  `model: opus` for security-critical PRs and the first review of a new security design, Sonnet
  for the rest, for tooling (one final Opus review if it changes secret handling) and for
  delta reviews unless the fix changes security-critical application code; AGENTS.md review
  gate, roadmap Amendments 4 and 13),
  never this session. Running the review is the default; don't ask whether to skip it.
- Your own check, a review skill in this session, green CI and passing tests are not a `PASS`.
  "I reviewed it" in chat is not one either: the owner can merge the PR themselves on GitHub.
- Any push after a `PASS` invalidates it. A merge-only update of `main` with no hand-resolved
  conflict is covered by the Architect's merge check (AGENTS.md review gate, item 3); any
  other push needs a fresh (delta) review.
- Merge only with `gh pr merge <n> --squash --match-head-commit <reviewed-sha>`. With the merge
  queue this enqueues the PR; the queue tests the merged result and lands the squash, whose title
  is the PR title (no `--subject`). The branch need not be up to date with `main`.
- Never `--auto`, never `--admin`, never an approving review, never a ruleset change, never a
  comment starting `Human review:`.

## Commits

- Conventional Commits 1.0.0 (see AGENTS.md).
- **No `Co-Authored-By` trailer.**
- Never commit `.env`, tokens, keys or anything under `secrets/`.

## This machine

Windows with Git Bash and Docker Desktop. Shell scripts are LF-only (`.gitattributes`).
Worktrees go in `../classbeat-wt/`.
