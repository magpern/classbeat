# Plan: Paddle on fakes, legal drafts and sign-up controls (M7b-prep)

Owner go 2026-11-12. Design: [ADR 0032](../adr/0032-paddle-on-fakes.md). Roadmap Amendment 21.
Builds on M7a ([commercial.md](commercial.md), ADR 0031). **Not in scope:** real Paddle keys,
the real HTTP client, real prices, publishing legal text, public sign-up on, the provider
agreements and the extra song-picking arrangement. Those are M7b-real and the owner's.

## PR split

| # | PR | Depends on | Developer / Reviewer |
|---|---|---|---|
| PB-0 | Plan, ADR 0032, Amendment 21, status, milestone and issues | owner's go | Architect / **Opus** |
| PB-1 | Pure `paddle_signature.py` and `paddle_events.py` with fixtures and table tests | PB-0 | **Opus / Opus** |
| PB-2 | `PaddleClient`, `PaddleProvider`, `FakePaddleClient`, `CLASSBEAT_BILLING_PROVIDER`, webhook route, config gates | PB-1 | **Opus / Opus** |
| PB-3 | Account > Plan "waiting for confirmation", fake hosted checkout page (test/compose only) | PB-2 | Sonnet / Sonnet |
| PB-4 | Legal page drafts (terms, privacy, refunds, sub-processors), en | PB-0 | Sonnet / Sonnet |
| PB-5 | Sign-up and cost controls: report what exists, close gaps behind `CLASSBEAT_PUBLIC_SIGNUP` | PB-0 | **Opus / Opus** |
| PB-6 | e2e done-when on the fake (buy, webhook, renew, cancel, replay, failed payment), runbook "connect real Paddle", status | PB-2, PB-3, PB-5 | Sonnet / Sonnet |

PB-1 to PB-2, PB-4 and PB-5 run in parallel.

## Done when
1. A tester buys a plan through the fake Paddle: checkout, signed webhook, plan active.
2. A replayed or duplicated webhook changes nothing beyond the first valid one; a delayed or reordered valid event is answered 409 until the earlier one lands, then applies exactly once.
3. A bad signature, an old timestamp and a price that disagrees with the policy are refused with
   no write and no secret in any log.
4. A failed payment leaves the plan unchanged and says so.
5. `paddle` and `billing_enabled = true` are refused by config; `fake_paddle` is refused in prod.
6. Legal drafts exist, are not public, and are listed for the owner.
7. The sign-up report is in the ADR or status; public sign-up is off by default.
8. A runbook lists exactly what the owner does to connect real Paddle.
