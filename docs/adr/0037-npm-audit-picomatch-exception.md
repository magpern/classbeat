# ADR 0037: a dated exception in the npm audit gate for picomatch GHSA-fc5q-39xc-vjpf (#1167)

Status: Accepted (Architect decision 2026-11-17, issue #1167; subject to review). Expires 2026-12-17.

Related: [docs/developer-commands.md](../developer-commands.md) "Security checks (CI)", AGENTS.md
"Decisions and ADRs".

## Context

On 2026-11-17 npm published GHSA-fc5q-39xc-vjpf: `picomatch` is vulnerable to ReDoS through
single-branch repeated extglobs (high, CVSS 7.5, CWE-1333, vulnerable range `<=4.0.7`). The `Checks`
job runs `npm audit --audit-level=high` in `frontend/` and `e2e/`, so `ci` went red on every pull
request. Only `frontend/` is affected: `e2e/` reports no vulnerabilities.

**No patched version exists.** The latest `picomatch` is 4.0.7 (published 2026-08-24), which is inside
the range; the 2.x and 3.x lines are too. The lockfile already holds 4.0.7, so `npm update` and an
`overrides` entry have nothing to move to. npm's own suggestion (`npm audit fix --force`, "vitest@3.1.4")
is a breaking downgrade that does not reach a patched `picomatch`. Seven packages are high in the report
(`picomatch`, `fdir`, `tinyglobby`, `vite`, `vitest`, `@vitejs/plugin-react`,
`@typescript-eslint/typescript-estree`); all of them inherit this one advisory (the `minimatch`
advisory GHSA-g3hv-75wg-m249 is moderate and not gated).

Reachability, checked on 2026-11-17:

- Everything that pulls `picomatch` in is build and test tooling: `vite`, `vitest`, `tinyglobby`,
  `fdir`, `@vitejs/plugin-react`, `typescript-eslint`. `npm ls picomatch --omit=dev` in `frontend/` is
  **not** empty: it lists `picomatch@4.0.7` under `@tailwindcss/vite` > `vite` > `tinyglobby` / `fdir`,
  because Tailwind's Vite plugin is a regular dependency. It is still build tooling.
- Nothing of it ships. The runtime image copies only `frontend/dist` (and three JSON files) out of the
  build stage (`Dockerfile`, `COPY --from=frontend`); `node_modules` never reaches it. A search of
  `frontend/dist` after `npm run build` finds no `picomatch`.
- The ReDoS needs an attacker-controlled extglob pattern. The only patterns these tools see are the ones
  in our own config and source tree, written by us; no user input reaches them.

## Decision

1. Replace the raw `npm audit --audit-level=high` step in the `checks` job with
   `scripts/npm-audit-gate.mjs frontend e2e`. It runs `npm audit --audit-level=high --json` in each
   directory and fails unless every high or critical package traces only to an allowlisted advisory.
   The decision logic is pure (`scripts/npm-audit-gate-policy.mjs`) and tested with fixture reports
   (`scripts/npm-audit-gate.test.mjs`, its own CI step).
2. The allowlist is one list in the policy file, keyed by advisory id, with a reason and a mandatory
   expiry (YYYY-MM-DD). The single entry is GHSA-fc5q-39xc-vjpf, expiry **2026-12-17** (30 days). A
   package is tolerated only if every high or critical advisory at the end of its `via` chains is
   allowlisted and not expired.
3. Everything else keeps `--audit-level=high` semantics. These fail the gate: any other high advisory,
   any critical advisory (never tolerable, even if listed), an expired entry (with a message naming the
   date), an allowlist entry without a valid expiry or reason, and any report the script cannot read
   (missing `auditReportVersion` 2, unknown package in a chain, unknown severity or advisory shape, an
   npm error object).

## Alternatives

- **Wait** for a patched `picomatch`: leaves every pull request and `main` red for an unknown time and
  blocks all other work.
- **Downgrade** (`npm audit fix --force`, vite or vitest older): breaking, and none of those versions
  reaches a patched `picomatch`, so it would not fix the audit.
- **Drop the vite chain** (another bundler and test runner): a large change to remove a dev-only risk.
- **Lower `--audit-level` or `--omit=dev`**: weakens the gate for every future advisory, and
  `--omit=dev` does not help (see Context: `@tailwindcss/vite` is a regular dependency).

## Consequences

- The exception expires on 2026-12-17: from 2026-12-18 the `npm audit` step fails again, with
  `allowlist expired 2026-12-17` in its output. Renewing needs a reviewed change with a fresh reason.
- Removing it when a patched `picomatch` ships (Dependabot's npm PR, or `npm update picomatch`):
  1. update the lockfile in `frontend/` and check that `npm audit --audit-level=high` passes
     without the allowlist (run it with the entry deleted);
  2. delete the `ALLOWLIST` entry (and, if no entry remains, keep the gate or restore the raw step);
  3. before relying on "dev tooling only" again, check that `frontend/dist` does not contain
     `picomatch` (`grep -rl picomatch frontend/dist`) and that the `Dockerfile` still copies only
     the build output into the runtime image.
- A stale entry (advisory fixed, entry left) is harmless but fails closed at the expiry date; it is
  removed in the next PR that touches the file, as for `.trivyignore`.
- The audit is still run, so a new high advisory, in the same packages or any other, still turns `ci`
  red at once.
