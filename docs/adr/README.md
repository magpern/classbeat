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
| [0007](0007-classes-revisions-and-saving.md) | Classes, revisions and saving to MOWL | Accepted |
| [0008](0008-song-picking.md) | Song picking: pool and final playlists, stages, jobs, approval (M4) | Accepted |
| [0009](0009-spotify-connection.md) | Spotify connection (BYOK), tokens and the Spotify API (M4) | Accepted |
| [0010](0010-live-activation-permissions.md) | Provider permissions for live activation | Accepted |
| [0011](0011-tiers-quotas-and-admin.md) | Tiers, quotas and the admin area (M5) | Accepted |
| [0012](0012-eval-suite-and-model-providers.md) | Eval suite, OpenAI and DeepSeek adapters, and the model enable gate (M3c) | Accepted |
| [0013](0013-ui-design-night-studio.md) | UI design: the Night Studio look, navigation and components | Accepted |
| [0014](0014-account-export-and-deletion.md) | Account data export and account deletion (M6, GDPR) | Proposed |
| [0015](0015-swedebeat-connection.md) | The Swedebeat connection: connect, status, disconnect, the app key (S1) | Accepted |
| [0016](0016-swedebeat-music.md) | Swedebeat music: "My Swedebeat mix" and Spotify-to-Swedebeat matching (S2) | Proposed |
| [0017](0017-class-pattern-soft-rules.md) | Class patterns as soft rules: profiles, warnings, evals (#388) | Proposed |
| [0018](0018-mowl-running-classes.md) | MOWL running classes next to cycling (M8, #373) | Accepted |
| [0019](0019-passkeys.md) | Passkeys with cross-device sign-in (#311; revised 2026-11-05, amends ADR 0003) | Proposed |
| [0020](0020-image-only-install.md) | Image-only install: db image, release assets, install checks (#519) | Proposed |
| [0021](0021-use-part-of-a-song.md) | Use part of a song: trim an intro or outro, start mid-track (#580); blocked, no source can play part of a song | Proposed |
| [0022](0022-account-emails.md) | Account emails: registry, branded multipart mail, fire-and-forget delivery, preferences (ME, #649) | Accepted, subject to review |
| [0023](0023-multilingual.md) | Multilingual support: English, Swedish and Danish UI and mail; agent-translated and agent-reviewed; classes stay English (ML, #657) | Accepted, subject to review |
| [0024](0024-mowl-qr-connect.md) | Connect MOWL by scanning a QR code: anonymous ticket, image from our origin, TLS-only MQTT on 8883, per-session wait (#647) | Proposed |
| [0025](0025-turnstile.md) | Cloudflare Turnstile on sign-in, sign-up, the provider-email step and the reset request: order before any account lookup, fail closed, web-only egress (#721) | Proposed |
| [0026](0026-scaling-music-analysis.md) | Scaling music analysis: cap, measure and cache now; more workers on one box; a separate analysis host; autoscaling only on a measured trigger (#733) | Proposed |
| [0027](0027-language-prefixed-urls.md) | Language-prefixed URLs `/en`, `/sv`, `/da` for the whole site: client redirect from old URLs, server head tags and sitemap, admin unprefixed (#719) | Accepted |
| [0028](0028-ui-refresh.md) | UI refresh: calmer screens, shorter copy, Connections as rows and dialogs, Swedebeat always listed, MOWL QR on by default (MX) | Accepted |
| [0029](0029-ai-first-class-design.md) | AI-first class design: free-text start and interpret stage, four MOWL games (Ghost Target removed), explicit vs assumed, web-side hand-off (MA) | Accepted |
| [0030](0030-public-pages-prerender.md) | Public pages: build-time prerender, `published` and `draft` paths, `createRoot` takeover with a release gate, document navigation at public boundaries (PP, #755) | Accepted, not implemented |
| [0031](0031-plans-and-dummy-billing.md) | Plans and billing with a dummy provider: Free / Individual / Professional, admins Professional, subscription writer on the audited maint path, proration, `billing_test` testers, prices on maint only (M7a, #945) | Accepted |
| [0033](0033-invite-requests.md) | Invite requests and invite codes that carry a tier: a public request form, the admin decides, one code granted through the redeem path at sign-up (PB-7, #985) | Accepted |
