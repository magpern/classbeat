# Swedebeat mix: the owner's media-host check

[ADR 0016](../adr/0016-swedebeat-music.md) section 4 and section 10, step 1. **Technical completion**
for path (a) is tested against the fakes only; this runbook is for the one authorized live check
that names the MOWL temporary-link media host and adds it to `MOWL_MEDIA_HOSTS` in a reviewed PR.

## Rules

- Agents run none of these steps without the owner's go-ahead for that step. Log it under "Live
  activation log" in [docs/status.md](../status.md) (the coordinator updates that file after merge).
- Never paste a temporary link, its query string or signature, a MOWL token or a mix file into chat,
  an issue or a log. The log line you need names **only the hostname**.
- Until the host is in `MOWL_MEDIA_HOSTS`, new mix analysis in production ends
  `media_host_not_allowed` (cached maps still work). That is intentional fail-closed behaviour.

## Before the check

- `main` is green and the A6 done-when suite is merged (backend tests and
  `e2e/tests/swedebeat-mix.spec.ts`).
- The owner's stack has MOWL connected and `SWEDEBEAT_ENABLED=true` with the admin flag allowing
  mixes (see [swedebeat-connect.md](swedebeat-connect.md)).
- Song picking and Swedebeat sign-in are separate: this check needs **MOWL only** for listing and
  attaching a mix; it does not need a Swedebeat token in classbeat.

## The steps

| # | Step | Who | Writes to a real account? | What it measures |
|---|---|---|---|---|
| 1 | With your own MOWL account, open **New class**, choose **Use my Swedebeat mix**, pick a mix that is **not** cached yet, and start a design. Let it fail with the sentence that the mix was added to MOWL but could not be analysed here yet. | Owner, or an agent with the go-ahead | Yes: MOWL attach of the mix | That MOWL lists Swedebeat mixes without the classbeat Swedebeat token; the job ends `media_host_not_allowed` |
| 2 | In the **worker** log, find the operator line for the refused media host (from the egress allowlist; hostname only, never the full link). Record the exact hostname(s). | Owner | No | The real MOWL temporary-link media host(s) |
| 3 | Open a PR that adds those exact hostnames to `MOWL_MEDIA_HOSTS` in `classbeat/egress.py` (https, port 443, no wildcard). The existing guard tests must be updated to pin the new set. Re-run one live analysis after deploy to confirm the job completes and the log shows `swedebeat media fetched` once, with no link text in logs. | Owner | Yes: one mix analysis | That production analysis works with the confirmed host |

## After the steps

- Do not widen the list beyond what MOWL returned on the live check. Each extra name is a reviewed
  change.
- If MOWL ever returns a different host, treat it as a new live check; do not guess or wildcard a
  cloud domain.
