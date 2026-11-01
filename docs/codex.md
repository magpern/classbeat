# Working on classbeat with Codex

## Start here

AGENTS.md is the shared rulebook, automatically discovered by Codex. Read it in full,
then roadmap.md, status.md and the issue's ADRs. Roadmap Amendment 10 maps the Claude
tiers to Codex; it changes development tooling only, not the application's model routing.
The existing Claude setup stays available.

The checked-in .codex/config.toml selects GPT-6 Astra with high reasoning for the
Architect / Orchestrator and raises project_doc_max_bytes to 131072: AGENTS.md is over
64 KB and must not be silently truncated. Trust the checkout in Codex so its project
configuration and custom agents load. Check instruction loading on the first session.
If your interface ignores project config, explicitly read the complete file from disk
and set the equivalent instruction budget in its supported configuration.

## Requirements

- A current Codex client with access to the configured models. Sign in using the client;
  keep its credentials outside the repository. Do not put a coding-agent credential in
  classbeat's secrets directory.
- Git and GitHub access to magpern/classbeat: read repository/issues/checks, push issue
  branches, create PRs and post review comments. Authenticate gh separately for CLI
  workflows; a connected GitHub tool can perform equivalent operations. Never display tokens.
- Bash (Git Bash on Windows), Docker Desktop or Docker Engine with Compose v2, uv
  (Python 3.12), and Node/npm matching the repository/CI versions. Use the locked
  dependency versions and commands in AGENTS.md and .github/workflows/ci.yml.
- Dependency downloads and Docker image pulls when setting up. Runtime tests use
  fakes and loopback/the isolated Compose network, never provider accounts.
- A local Docker-capable environment for database and Playwright stack tests. A hosted
  environment without Docker can prepare a PR and run available targeted checks, then
  rely on CI for the stack checks. Report precisely which checks could not run.
- Read access to magpern/mowl-workouts only if rebuilding the vendored wheel is needed;
  use scripts/build-mowl-wheel.sh and the pinned source described in AGENTS.md.

No real MOWL, Spotify, Swedebeat or application model keys are needed for fake-only
development. scripts/worktree.sh generates local secrets and empty provider placeholders.
Do not enable application OpenAI routes as part of Codex setup.

## Roles and independent review

.codex/agents/ contains native standalone custom-agent TOML definitions:
developer (Sol), security-developer (Astra), explorer (Luna), reviewer (Sol) and
security-reviewer (Astra). The primary session is the Architect; use project config's
Astra/high setting. Each role keeps the shared authority boundaries and risk rules.

At startup verify that your client discovers these agents and can select their models.
They are not executable orchestrators: the Architect must assign issues, worktrees and
review requests. Each writer owns one issue branch and one worktree. Do not run Claude
and Codex writers on the same branch.

A reviewer must start with no inherited author/Architect conversation; supply the PR,
head SHA, issue and repository evidence instead. If the interface can only fork the
author's history, open a separate fresh Codex session for review. Never replace this
with the author's own /review. Reviewers may inspect files and run targeted checks in
a disposable worktree, but never fix the PR. They post a comment:
`Agent review (GPT-6.1 Sol): PASS <full head SHA>` (or FAIL; Astra names itself GPT-6 Astra).
Use the actual selected model. The same risk classifications, eligible delta reviews,
mutation checks and second-FAIL escalation in AGENTS.md apply.

If a model or independent session is unavailable, leave the PR open with review pending.
Green CI alone is never permission to merge. After an independent current-SHA PASS and
green CI, only the Architect enqueues the PR with:
`gh pr merge <n> --squash --match-head-commit <reviewed-sha>`.
Never use --auto, --admin, approving reviews or ruleset changes to bypass the gate.

## First working session

1. Read the shared rules, roadmap/status, open PRs and issues; inspect git worktree list.
   Resume the next unblocked issue, without re-planning completed milestones.
2. Use `bash scripts/worktree.sh new <issue> <slug>` from the checkout, then change into
   the printed worktree. Its branch, ports, database volume and secrets are isolated.
3. Follow AGENTS.md's backend/frontend commands. Run lint, type checks and targeted
   tests for the change. Use its Compose/e2e instructions when required; CI runs the
   full suite. Do not weaken a check to make it pass.
4. Create one PR closing the issue, with scope, each acceptance criterion and its test
   evidence. Use Conventional Commits and no Co-Authored-By trailer. Codex PRs end with:
   `🤖 Generated with [Codex](https://developers.openai.com/codex/)`.
5. Wait for CI, obtain independent review, fix blocking findings and request the required
   delta review. After merge, update durable status and clean up that issue's worktree.

The shared safety envelope still requires specific owner authorization for deployment,
DNS/TLS, paid services, provider dashboards, real-account writes/tests and contacting people.
Do not broaden host permissions or disable the sandbox to work around missing capabilities.
Report an environment blocker and continue independent work instead.

## Official configuration references

- [Instruction discovery](https://learn.chatgpt.com/docs/agent-configuration/agents-md)
- [Custom subagent TOML and isolation](https://learn.chatgpt.com/docs/agent-configuration/subagents)
- [Configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference)

Project configuration is client-dependent. This PR validates TOML syntax and documented
schema, not a particular local Codex installation or the owner's account/model access.
