# Economical Cursor alternative

This is an optional development-agent profile alongside Claude Code and Codex.
It does not change classbeat's application model providers, model routes or data policy.
Read AGENTS.md in full, the roadmap, status and relevant ADRs before working.
Roadmap Amendment 11 authorizes the model substitutions for Cursor sessions.

## Role selection

| Role | Cursor selection | Agent |
|---|---|---|
| Architect / Orchestrator (primary session) | Grok 4.7, high effort, standard speed | Primary session |
| Routine Developer | Composer 2.5, standard | cursor-developer |
| Explorer | Composer 2.5, standard | cursor-explorer |
| Routine Reviewer | Grok 4.7, high effort, standard | cursor-reviewer |
| Security-critical Developer | Grok 4.7, high effort, standard speed | cursor-security-developer |
| Security-critical Reviewer; review after a second FAIL | Grok 4.7, high effort, standard speed | cursor-security-reviewer |

This is a cost-oriented starting point, not a claim that the models are equivalent.
Use the first routine PRs to assess acceptance-criterion coverage, blocking review findings,
fix cycles and total usage. Grok 4.7 High implements security-critical Cursor issues and, in a
different fresh context, reviews them, including a review after a second failure. The Grok
Architect can classify and schedule that work but does not make its production security
decisions: those belong to the Cursor security developer. Routine implementation stays
Composer. Claude Code and Codex keep Opus for the same work.

## Configure Cursor

1. Open the checkout in a current Cursor release. Select Grok 4.7, high effort and
   standard speed for the primary Architect. A rules file cannot force the primary picker.
2. Confirm .cursor/rules/classbeat.mdc is active and all five cursor-* agents are visible.
   Invoke those names explicitly. Cursor can also discover .claude/agents/ and .codex/agents/;
   unique cursor-* names avoid changing those profiles or depending on name precedence.
3. The frontmatter pins Composer with composer-2.5[fast=false] and Grok with
   grok-4.7[reasoning_effort=high,fast=false]. The security developer and the security reviewer
   both use the Grok pin, in separate fresh contexts. Routine development uses the Composer pin.
   Claude Code and Codex keep Opus.
4. Verify the actual model on every task card and in the usage dashboard. Plan limits,
   admin restrictions and legacy Max Mode settings can cause Cursor to substitute another
   model even when it is explicitly configured. If it substitutes, do not proceed under
   the expected model's identity; report the blocked role.
5. Cursor security work does not use Opus. Claude Code and Codex still do. Do not enable paid
   services or change plans without the owner's specific authorization.

The model selections are versioned defaults. If an ID or parameter is unavailable, report
it and make a reviewed configuration update rather than silently switching to Auto.

## Environment and workflow

Use Git, Bash (Git Bash on Windows), Docker Desktop/Engine with Compose v2, uv/Python 3.12
and the Node/npm versions used by CI. Follow AGENTS.md's locked dependency, database,
frontend and Playwright commands. GitHub access must allow reading issues/checks and
creating issue branches/PRs and review comments; authenticate gh outside the repository.

Create an issue branch and worktree with scripts/worktree.sh new <issue> <slug>; run each
writer's isolated stack there. Never put Claude, Codex and Cursor writers on one branch.
Developers run lint/type checks and targeted tests; CI runs the full suite. Tests use fakes,
local Postgres and the isolated Compose network. No real provider accounts or model API
keys are needed. Report checks that the local environment cannot run, and use CI evidence;
do not claim unrun checks passed. If the vendored MOWL wheel must be rebuilt, follow its
pinned-source instructions and obtain read access to magpern/mowl-workouts as needed.

The Architect continues with unblocked issues and records durable progress in status.md.
It escalates product decisions and actual external blockers under the shared rules.
Deployments, DNS/TLS, paid services, provider dashboards, live-account tests/writes and
contacting people retain their specific owner-authorization requirements.

## Review and merge

Each reviewer starts with a fresh context, without author or Architect conversation.
Supply the PR, current head SHA, issue and repository evidence, not the author's conclusions.
Cursor documents clean subagent contexts; if an interface cannot provide that isolation,
open a separate fresh Cursor session. A self-review or generic in-session review is not PASS.

Grok 4.7 High reviews every Cursor PR, including security-critical PRs, their tests/docs-only
deltas, uncertain classifications and reviews after a second FAIL. The security categories in
AGENTS.md remain the classification source.
About eight risk-based mutations per review plus the ones earlier findings require,
targeted checks and current-SHA green CI are required as described in the shared gate.

Verdicts remain PR comments, for example:
Agent review (Grok 4.7): PASS <full head SHA>
Agent review (Grok 4.7): FAIL <full head SHA>
Use the actual model name. No approving review, no Human review: comment, and no override
of FAIL. Leave review pending when the required independent role cannot run.

Only the Architect may enqueue a merge, after current-head PASS, green CI and no blocking
findings, with gh pr merge <n> --squash --match-head-commit <reviewed-sha>.
Keep the shared merge-only carry-forward and new-head review rules; never use --auto,
--admin or change rulesets to bypass them.

Cursor-authored PRs state scope, acceptance criteria and test evidence, close their issue,
use Conventional Commits and end with:
🤖 Generated with [Cursor](https://cursor.com/)
No Co-Authored-By trailer. Other tools retain their own attribution.

## References and verification

- [Cursor custom agents and model parameters](https://cursor.com/docs/subagents)
- [Cursor project rules](https://cursor.com/docs/rules)
- [Composer 2.5](https://cursor.com/docs/models/cursor-composer-2-5)
- [Grok 4.7](https://cursor.com/docs/models/grok-4-7)

Frontmatter and local references can be checked without Cursor. Agent discovery, model
availability, speed/effort selection and real billing require a smoke check in the owner's
Cursor installation. No such client check is claimed by this configuration PR.
