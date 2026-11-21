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
| [0015](0015-swedebeat-connection.md) | The Swedebeat connection: connect, status, disconnect, the app key (S1) | Accepted; §4 and §7 superseded by ADR 0035 |
| [0016](0016-swedebeat-music.md) | Swedebeat music: "My Swedebeat mix" and Spotify-to-Swedebeat matching (S2) | Proposed |
| [0017](0017-class-pattern-soft-rules.md) | Class patterns as soft rules: profiles, warnings, evals (#388) | Accepted |
| [0018](0018-mowl-running-classes.md) | MOWL running classes next to cycling (M8, #373) | Accepted |
| [0019](0019-passkeys.md) | Passkeys with cross-device sign-in (#311; revised 2026-11-05, amends ADR 0003) | Proposed |
| [0020](0020-image-only-install.md) | Image-only install: db image, release assets, install checks (#519) | Proposed |
| [0021](0021-use-part-of-a-song.md) | Use part of a song: trim an intro or outro, start mid-track (#580); blocked, no source can play part of a song | Superseded for SwedeBeat by 0036; Spotify still not allowed |
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
| [0034](0034-classbeat-player-connection.md) | classbeat-player: classbeat as an OIDC provider for the player, signed events, the `player` connection, the class-format converter and "Send to player" (#1054) | Proposed |
| [0035](0035-swedebeat-gateway.md) | The SwedeBeat gateway: one configured origin (production `http://192.168.1.222:7355`; plain http only to loopback or private IPs), a client-key secret file in web and worker, connect stores the bearer with the sign-out consent up front, one `/catalog/match` per job re-thresholded at 0.85, the SwedeBeat app key and hosts removed (SG, #1075; supersedes ADR 0015 §4 and §7 and parts of ADR 0016 §4 and §5) | Accepted (implemented on fakes, SG-1 to SG-5) |
| [0036](0036-swedebeat-studio.md) | The SwedeBeat studio: one model for music and workout; match with evidence and undoable swaps; per-track analysis; mix spec (order, cuts, crossfades) in the draft; outputs per class (Spotify playlist, SwedeBeat playlist, mix, MOWL) with per-output state and gates; classbeat renders, and the gateway stays a proxy (SR; builds #580 for SwedeBeat) | Accepted (not implemented) |
| [0037](0037-npm-audit-picomatch-exception.md) | A dated exception in the npm audit gate for picomatch GHSA-fc5q-39xc-vjpf (no patched version; dev tooling only): allowlist in one place with a mandatory expiry (2026-12-17), a critical advisory never tolerated, removal steps (#1167) | Accepted, expires 2026-12-17 |
| [0038](0038-composer-flow.md) | The composer flow: describe with attached music and pinned songs, a sketch revised by chat, songs filled and designed before one approval, approve and save in one step with a no-model fit, only Spotify playlists attachable; behind `composer_flow` (#1184) | Accepted (not implemented) |
| [0039](0039-discard-failed-first-creation.md) | A class whose first creation (the start screen's reading, or its first design) failed or was stopped before anything was made is discarded in the ending's transaction under the class lock (an allowlist); the ended job stays readable, detached (`jobs.discarded_class_id`), and the class page says why (amends ADR 0029 section 5) | Proposed |
| [0040](0040-spotdown-analysis-service.md) | spotdown as classbeat's music-analysis service: match → select → analyse → refine → design, timing classes with a strict `exact`, a generic spotdown with the v2 API and two scoped lookups, schema v1 with measured/estimated/heuristic, provenance, four cache layers, a bounded failure taxonomy, quality gates and a per-source rollout; amends ADR 0010 and ADR 0021 §2 (the owner accepted #1296 on 2026-11-20, roadmap Amendment 32); every phase authorized by the owner on 2026-11-20 except #1296 to #1299 and spotdown#1; the amendment to ADR 0010 and ADR 0021 §2 took effect on 2026-11-20 (SD-P0, #1300) | Accepted |
| [0041](0041-user-audio-music-source.md) | `user_audio`, a class built on the user's own audio files: relayed through classbeat to spotdown and never persisted (§1), analysed under a per-track scope, `exact` only for the attested played file, caps and quotas, retention and deletion, no cross-user reuse (SD-P7b, #1305) | Proposed |
| [0042](0042-class-preview-and-editor.md) | The Class Preview & Editor: audition and edit a class before outputs are made; one editor with interchangeable transports (SilentTransport, rendered SwedeBeat windows from the existing renderer, Spotify Connect remote control of original tracks); workout edits as revisions with overrides, locks, anchors and optimistic concurrency; cut pins (requested, effective, confirmed); a 15-minute listening session counted in the browser, a deterrent, not DRM; amends ADR 0007, 0009, 0016 and 0036 (plan [class-preview-editor.md](../plans/class-preview-editor.md)) | Accepted by the owner (2026-11-21), subject to review |
