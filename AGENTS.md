# AGENTS.md: how agents build classbeat

classbeat is a website where indoor-cycling instructors describe a class, Claude picks the
songs and designs it, and the class is saved to their Intelligent Cycling (MOWL) account.
It is built autonomously by agents. The owner (`@magpern`) makes product decisions and gives
external approvals; agents do everything else.

**[docs/roadmap.md](docs/roadmap.md) is the frozen plan (Revision 6, with its dated amendments at the top). If this file and the
roadmap differ, the roadmap wins.** Section numbers (§1, §4, M2 …) refer to it.

## Codex entry point

Codex must read this file **in full**, then [docs/codex.md](docs/codex.md), the roadmap
and status before work, and [docs/developer-commands.md](docs/developer-commands.md) for the
area it touches. The Codex model mapping and PR attribution are authorized by
roadmap Amendment 10; the Claude role definitions below remain valid for Claude Code.
Codex role definitions are in `.codex/agents/*.toml`. Use the same issue, worktree,
CI, independent current-head review and merge gates. Never treat an in-session
`/review` as the independent Reviewer. The first review of a PR is a fresh reviewer session
and later rounds resume it (review gate, item 3). If a fresh reviewer cannot be launched,
leave the PR open and report that review is pending.

## Roles

| Role | Model class | Owns | Must not |
|---|---|---|---|
| Architect / Orchestrator | Opus | The next milestone, its PR split and acceptance criteria, dependencies, scheduling, merge order, ADRs, track F, `docs/status.md` | Normally write production code |
| Developer | Sonnet; Opus for security-critical PRs (below) | One PR in its own branch and worktree: code, tests, CI fixes, **all** reviewer findings | Change architecture or acceptance criteria without the Architect |
| Explorer | Haiku | Bounded work: searches, evidence, fixtures, docs, extra tests | Own security, auth, isolation, crypto or billing decisions |
| Reviewer | a **fresh** context for the first review of a PR, **resumed** (same agent) for its later rounds (Opus; Sonnet for non-security PRs, see the review gate), sharing no context with the Architect or Developer | The diff, the acceptance criteria, tests, CI output, ADR compliance → `PASS` or `FAIL` with findings | Fix its own findings; review an ADR it wrote |

Model *classes*, not versions: use the current generation of each tier. A model substitution
never widens a role's authority. Agent definitions live in `.claude/agents/`.

**Security-critical PRs get an Opus Developer** (owner decision, 2026-10-28): encryption and key
handling, jobs with leases and fencing, Spotify OAuth, and approvals and external writes. The
issue says so in its "Owner:" line; everything else keeps a Sonnet Developer. The Reviewer is a
fresh context for a PR's first review either way, never the Developer's: Opus for those PRs and the others listed in
the review gate. **Tooling is not security-critical by default** (roadmap Amendment 13, owner,
2026-11-03): compose files, deployment and CI/guard scripts, runbooks and the like have a Sonnet
Developer and a Sonnet Reviewer, except that a change to how secrets are mounted, exposed or
read (file modes or groups, secret handling in config) gets one final Opus review of the final
head after the Sonnet rounds, never an Opus Developer.

## Unit of work: one issue, one PR

- Every milestone is a GitHub milestone. The Architect splits it into issues, one per PR,
  each with scope, acceptance criteria and dependencies ("Depends on #n").
- A PR closes one issue (`Closes #n` in the body) and states its scope, its acceptance
  criteria and how they were tested.
- A milestone is done when all its issues are closed and its "done when" in the roadmap holds.

## PR loop

```
issue → Developer (+ Explorer) in its own worktree → PR → CI
      → fresh Reviewer → PASS → agent merges
                       → FAIL → Developer fixes every blocking finding → CI → fresh delta Reviewer
```

If the same blocking finding survives three cycles, the Architect re-assesses the root cause,
revises the issue or ADR, and the loop restarts with a fresh Reviewer.

The Orchestrator keeps going without routine human input: it does not stop for a `FAIL` or at
the end of a milestone. It escalates only genuine blockers (see "Escalation" below).

## Review gate (mandatory for every PR an agent merges)

1. The PR has a Reviewer `PASS` for its **current head commit**, recorded on the PR as a
   comment that starts `Agent review (<model>): PASS <head SHA>` followed by the non-blocking
   findings. A `FAIL` is recorded the same way: `Agent review (<model>): FAIL <head SHA>`.
2. The Reviewer was a fresh context for the PR's first review: **Opus** for security-critical PRs (encryption and keys,
   auth and sessions, tenant isolation and RLS, guards and roles, jobs with leases and fencing
   and the queue SQL, approvals and external writes, Spotify OAuth, egress and model keys,
   billing); **Sonnet** is enough for the rest (UI, tests, docs, tooling; Amendment 13 for
   tooling that touches secret handling). The first review of any new security design is Opus.
   When in doubt, Opus.
3. **A new head SHA needs a new review**, scoped as follows (roadmap Amendment 2):
   - *Merge-only update:* the only change since the reviewed head is a merge of `main` with
     no hand-resolved conflict (`git diff <reviewed>..<head>` equals `main`'s own change), and
     CI is green. The Architect records `Agent review (merge check): PASS <head SHA> (carries
     <reviewed SHA>)`. If `main` changed a module the PR changes or calls, or a conflict was
     resolved by hand, a fresh reviewer checks the merge (scoped review).
   - *Fix round after a `FAIL`:* a **delta review** of the diff since the last reviewed head,
     verifying each earlier finding and whatever the delta touches.
   - *Reviewer reuse (owner decision, 2026-11-04, roadmap Amendment 14):* the first review of
     a PR is a fresh context. Every later round on the same PR (a delta review after a `FAIL`,
     a scoped review after a hand-resolved merge) **resumes the same Reviewer agent** (send it
     the new head SHA) instead of spawning a new one, on security-critical PRs too: the
     Reviewer stays Opus there and stays independent of the Developer and the Architect. A
     fresh Reviewer is used only after a second `FAIL` on the same PR (the Architect first
     re-assesses the design, for guard checks allowlists over blocklists), or when the
     Reviewer cannot be resumed; the review comment then says so. A delta that only adds or
     changes tests or docs, loosens no guard test and has no hand-resolved conflict in
     production code may still be reviewed by Sonnet even on a security-critical PR (roadmap
     Amendment 4), as an option; resuming the same Opus Reviewer is the default.
   Reviewers rely on CI for the full suite and run only the targeted test files of what
   changed. **Mutation checks** only for the guarantees a PR adds or a fix changes, at most
   about 8 per review, chosen by risk, not a sweep of every line; a docs-only or test-only
   delta gets none beyond the earlier finding it addresses (verified by reading, or by one
   mutation of the code the new test protects). A docs-only review needs no throwaway
   database. Non-blocking findings are batched into few hardening PRs.
4. Not a `PASS`: the implementing session's own check, a review skill run in the same context,
   green CI, passing tests, or "it's fine" in chat. A human saying so in chat doesn't replace
   the review for an agent merge either.
5. Agents record verdicts only as comments. They never submit approving reviews and never
   write a comment starting `Human review:`.
6. A `FAIL` is never overridden by an agent.

**A human may merge any PR on GitHub regardless of the verdict.** That is the owner's
decision, never an agent's: an agent never merges without the gate, even when told to skip it.

## Merging

Agents merge only with:

```bash
gh pr merge <n> --squash --match-head-commit <reviewed-sha>
```

and only when the PR's **current head** has green required checks (`ci` and `pr-title`), the
`PASS` is for that SHA and no blocking comment is open. `--match-head-commit` makes GitHub refuse
the merge if the head moved after the review. The squash commit's title is the PR title (with
` (#<n>)`), so there is no `--subject`, and the PR title must be the Conventional Commits subject
(`pr-title` checks it on the PR). Never `--auto`, never `--admin`, never an approving review,
never a change to the ruleset to get a merge through.

The ruleset on `main` (GitHub Pro): pull request required, required checks `ci` and `pr-title`,
squash only, no direct pushes, zero required approvals, and **no requirement that the branch is up
to date** (owner, 2026-11-04: every merge made every other open PR "behind" and cost it a full CI
run). There is **no merge queue** (not available on this repository). `CODEOWNERS` (`* @magpern`)
is informational.

So a PR is merged on its own green head and its `PASS`, without updating the branch. Merge `main`
into a PR branch only for a real conflict (GitHub says the PR is "dirty") or when `main` changed
files the PR changes or calls in a way that needs checking; that new head is then reviewed again
(review gate, item 3). The safety net for a PR that was green alone but breaks with something
merged earlier is the full CI run on `main` after every merge (see "CI: what runs, and when"):
a red `main` is fixed forward first, before other merges.

## Branches, worktrees and commits

- Every task is a feature branch `<issue>-<slug>` and a PR. One writing agent per branch.
- Parallel developers each work in their own worktree: `scripts/worktree.sh new <issue> <slug>`
  creates `../classbeat-wt/<issue>-<slug>` with its own `.env`, so each runs its own stack
  (`COMPOSE_PROJECT_NAME=classbeat-<issue>`, own Postgres volume and ports). Remove the
  worktree after the merge (`scripts/worktree.sh rm <issue>`).
- When a dependency merges first, the dependent branch is updated from `main` and reviewed
  again (rule 3 above).
- **Conventional Commits 1.0.0** for commits, PR titles and squash subjects:
  `feat(auth): …`, `fix(worker): …`, `refactor(db): …`, `test(security): …`, `ci: …`,
  `docs: …`, `build: …`, `chore: …`. Breaking changes use `!` or a `BREAKING CHANGE:` footer.
  CI checks the PR title with commitlint (`commitlint.config.cjs`).
- No `Co-Authored-By` trailer in commits. PR descriptions end with
  `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.

## Safety envelope

**Agents may, without asking:** create branches, PRs and issues; review and merge per the gate;
cut releases and push images to `ghcr.io/magpern/classbeat`; run local Docker Desktop stacks;
make read-only checks needed by track F; branch, PR, merge (same gate) and release in
`magpern/mowl-workouts`, keeping its `CLAUDE.md` rules and its CLI, MCP server and plugin working.

**Only with the owner's authorization for that specific action:** deploying to the VPS;
DNS and TLS; paid services and subscriptions; provider dashboards (Spotify apps and
allow-lists, Paddle, Google/GitHub/Microsoft OAuth apps); emails to real people; any test run
against a live account; anything that writes to a real MOWL, Spotify or Swedebeat account;
contacting providers.

## Releasing

Agents may cut releases without the owner (safety envelope above). Deploying is not part of
a release and still needs the owner's authorization.

1. `main` is green and holds everything the release should contain. Pick the next SemVer
   version `vX.Y.Z` (a pre-release is `vX.Y.Z-rc.1`).
2. Tag the current tip of `main` and push the tag:

   ```bash
   git fetch origin main
   git tag -a vX.Y.Z -m "vX.Y.Z" origin/main
   git push origin vX.Y.Z
   ```

3. `.github/workflows/release.yml` then verifies the tag is SemVer and on `main`, builds
   `linux/amd64` and `linux/arm64`, pushes `ghcr.io/magpern/classbeat` with tags `X.Y.Z`,
   `X.Y` (not for pre-releases) and `sha-<short>` plus provenance and SBOM attestations, and
   also pushes `ghcr.io/magpern/classbeat-db` (Postgres with the init, queue and maint SQL; same
   tags), and creates the GitHub Release with generated notes (marked pre-release if the tag has a
   hyphen) and the deploy files as assets (`scripts/release-assets.sh`, `SHA256SUMS`; ADR 0020:
   a server needs no clone, `docs/runbooks/vps-deploy.md`). There is no `latest` tag; deployments
   pin a version.
4. Check the run with `gh run list --workflow release.yml`. The packages stay private; never
   change their visibility. Never move or delete a pushed tag: fix forward with a new version.

## Security rules

- Never commit, print or log secrets: API keys, OAuth client secrets, session cookies, MOWL,
  Spotify or Swedebeat tokens, encryption keys, signed URLs. `.env` files are never committed;
  `.env.example` holds placeholders only.
- Passwords for MOWL and Swedebeat are typed by the user on our own page, used once to get a
  token and discarded. Never stored, logged, put in a URL or sent to a model.
- No Spotify Client Secret, ever: Spotify sign-in is Authorization Code with PKCE (§3).
- Never use credentials embedded in third-party apps (for example Swedebeat's AWS keys).
- Tokens never go to a model. Model output is a proposal; the backend authorizes every write
  against the authenticated user (§2).
- Everything we create in a user's accounts is private: playlists `public: false`,
  `collaborative: false`; MOWL workouts private.
- Tests and CI never touch the network or real accounts: they use fakes for MOWL, Spotify,
  Swedebeat and model APIs.
  The backend test suite enforces it: an autouse fixture (`tests/netguard.py`) makes every
  non-loopback `connect` and name lookup raise before anything is sent; only a test marked
  `allow_network` (none today) is exempt.
- Every owned row carries `owner_id` and is read through `UserContext` (§1, ADR 0002).
- Trivy accepted risk (unfixed HIGH/CRITICAL findings are not gated) and the rest of the CI
  security checks: [docs/developer-commands.md](docs/developer-commands.md), "Security checks (CI)".

## Decisions and ADRs

Routine technical decisions never go to the owner. When a fact is unknown, the Architect picks
the safest reversible default, isolates it behind an interface or setting, and records it in
`docs/adr/NNNN-title.md` (Context, Decision, Alternatives, Consequences, Status). A fresh
Reviewer reviews the ADR in its PR.

## Escalation to the owner

Only for: external permissions or agreements (track F), credentials agents can't provision
safely, product decisions, and live-account tests needing authorization. Record the question
in `docs/status.md` under "Waiting for the owner", carry on with independent work, and resume
when it's answered.

## Cursor alternative

For Cursor sessions, read [docs/cursor.md](docs/cursor.md) and use the explicit
`cursor-*` roles in `.cursor/agents/`. Roadmap Amendment 11 authorizes Composer
for routine implementation and Grok 4.7 High for security-critical implementation,
coordination, and every review. Claude Code and Codex keep Opus for security-critical
work. The shared
worktree, CI, independent current-head review, merge and safety gates still apply.
Verify the actual model used; a client substitution blocks that role. Claude Code
and Codex keep their own profiles.

## Durable state and resuming

The repository and GitHub are the source of truth, never chat history:
`docs/roadmap.md`, `docs/status.md` (updated by every merge), `docs/adr/`, GitHub milestones,
issues and PR descriptions.

To resume after a context reset:

1. read `CLAUDE.md`, `AGENTS.md`, `docs/roadmap.md` and `docs/status.md`;
2. inspect open PRs, their CI and review comments, open issues, `git worktree list`;
3. continue with the next unblocked issue without re-planning finished work.

## Developer commands

Settings, per-feature behaviour, e2e and CI details: [docs/developer-commands.md](docs/developer-commands.md)
(read the part for the area you touch). The essentials:

```bash
# Backend, from backend/ (uv installs Python 3.12; CI uses `uv sync --locked`)
uv sync && uv run ruff check && uv run ruff format --check && uv run pyright
uv run pytest path/to/test_file.py     # targeted tests; database tests skip unless the Postgres below is up
# Throwaway Postgres for database tests (one name and port per worktree; Git Bash: export MSYS_NO_PATHCONV=1)
docker run -d --rm --name classbeat-5-pg -e POSTGRES_PASSWORD=test -p 127.0.0.1:55405:5432 postgres:17.11
docker exec classbeat-5-pg psql -U postgres -c 'CREATE DATABASE classbeat'
docker exec classbeat-5-pg sh -c 'echo owner-pw > /tmp/owner; echo app-pw > /tmp/app'
docker cp ../docker/postgres/init/10-roles.sql classbeat-5-pg:/tmp/10-roles.sql
docker exec -e DATABASE_OWNER_PASSWORD_FILE=/tmp/owner -e DATABASE_PASSWORD_FILE=/tmp/app \
  classbeat-5-pg psql -v ON_ERROR_STOP=1 -U postgres -d classbeat -f /tmp/10-roles.sql
export CLASSBEAT_TEST_DATABASE_URL=postgresql+psycopg://postgres:test@127.0.0.1:55405/classbeat
docker stop classbeat-5-pg             # when done
# Frontend, from frontend/ (Node 24)
npm ci && npm run lint && npm run typecheck && npm test && npm run build
# Stack and worktrees
scripts/worktree.sh new <issue> <slug>  # ../classbeat-wt/<issue>-<slug>, own .env, ports and volume; `ls`, `rm <issue>`
docker compose up -d --build --wait     # web on http://127.0.0.1:$WEB_PORT; `docker compose down -v` to stop
```

CI runs the full suite; locally run lint, types and the targeted tests only. A docs-only
change needs no database.
