# Architecture decision records

One file per decision: `NNNN-title.md`, with Context, Decision, Alternatives, Consequences
and Status (Proposed, Accepted, Superseded by NNNN). The Architect writes them; a fresh
Reviewer reviews each in its PR (AGENTS.md, "Decisions and ADRs").

| # | Decision | Status |
|---|---|---|
| [0001](0001-stack-and-layout.md) | Stack and repository layout | Accepted |
| [0002](0002-tenant-isolation.md) | Tenant isolation with UserContext and Postgres RLS | Accepted |
| [0003](0003-accounts-and-sessions.md) | Accounts, sessions and sign-in | Accepted |
| [0004](0004-connections-and-encryption.md) | Provider connections and envelope encryption | Accepted |
| [0005](0005-jobs-and-leases.md) | Jobs, leases and external operations | Proposed |
| [0006](0006-agent-runs.md) | Agent runs: provider interface, stages, limits and provenance | Proposed |
| [0010](0010-live-activation-permissions.md) | Provider permissions for live activation | Accepted |
