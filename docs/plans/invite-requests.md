# Invite requests (PB-7 #985, PB-8 #986)

Design: [ADR 0033](../adr/0033-invite-requests.md). Owner request 2026-11-12.

| Step | PR | What |
|---|---|---|
| PB-7 | #985 | Backend: migration `0047_invite_requests`, `POST /api/auth/invite-request`, the admin API on the gate, approve with a tier through the redeem path, the `invite_code` mail (en, sv, da), the purge. |
| PB-8 | #986 | Frontend: the request form on the sign-up page while the mode is `invite` (Turnstile action `invite_request`), and the admin's Invite requests screen (list, approve with a tier, decline, copy the code once). |

## API for PB-8

Public (`/api/auth/invite-request`, CSRF and Origin like every unsafe call):

```
POST {email, heard_from?, instructor: bool, locale?, captcha_token?}
202 {"status": "received"}            always, whatever the address
400 captcha_failed | 503 captcha_unavailable | 429 (Retry-After) | 403 requests_closed | 422
```

Admin (`/api/admin/invite-requests`, step-up for the POSTs, `no-store`):

```
GET ?state=pending|issued|declined&offset=0&limit=50
  200 {requests: [Request], next_offset: int | null, pending: int}   (pending: the whole queue, #989)
POST /{id}/approve {plan_id?: str, grant_days?: 1..366, expires_days?: 1..30 (14), send_email?: bool}
  201 {code: str, request: Request, email: "queued" | "mail_off" | "not_requested"}
  404 not_found | 409 already_decided | 422 unknown_plan or a bad body (plan_id and grant_days together)
POST /{id}/decline
  200 Request | 404 not_found | 409 already_decided
POST /decline {ids: [uuid], 1..100}   (#989; one audited transaction and audit row per request)
  200 {declined: [uuid], skipped: [uuid]}   (skipped: gone or decided meanwhile) | 422 a bad body

Request = {id, email, heard_from, instructor, locale, has_account, state, plan_id, grant_days,
           invite: {id, state: active|used|expired} | null,
           decided_by: {id, email | null} | null, decided_at, created_at}
```

Show `code` once (copy button), `heard_from` as text only. The plan list comes from
`GET /api/admin/plans`.
