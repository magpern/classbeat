import { get, post } from './client'
import { flag, list, object, optionalText, text, optionalCount, type JsonObject } from './decode'

// The admin Invite requests screen (ADR 0033; backend/src/classbeat/admin/invite_requests.py,
// PB-7 #985). Every route is behind the admin gate and the POSTs need a step-up. `heard_from` is
// free text from a stranger: it is only ever rendered as text. The approve answer carries the
// invite code once; the caller shows it and drops it.

const BASE = '/api/admin/invite-requests'

/** The pending queue's bound on the server (`PENDING_CAP`, invite_request_policy.py). */
export const PENDING_CAP = 1000

export type RequestState = 'pending' | 'issued' | 'declined'

export interface InviteRequest {
  id: string
  email: string
  heardFrom: string | null
  instructor: boolean
  locale: string | null
  hasAccount: boolean
  state: string
  planId: string | null
  grantDays: number | null
  invite: { id: string; state: string } | null
  decidedBy: { id: string; email: string | null } | null
  decidedAt: string | null
  createdAt: string
}

export type EmailOutcome = 'queued' | 'mail_off' | 'not_requested'

export interface Issued {
  code: string
  request: InviteRequest
  email: EmailOutcome
}

function decodeRequest(value: unknown): InviteRequest {
  const item = object(value, 'invite request')
  const invite = item.invite === null || item.invite === undefined ? null : object(item.invite, 'invite')
  const by =
    item.decided_by === null || item.decided_by === undefined ? null : object(item.decided_by, 'person')
  return {
    id: text(item, 'id'),
    email: text(item, 'email'),
    heardFrom: optionalText(item, 'heard_from'),
    instructor: flag(item, 'instructor'),
    locale: optionalText(item, 'locale'),
    hasAccount: flag(item, 'has_account'),
    state: text(item, 'state'),
    planId: optionalText(item, 'plan_id'),
    grantDays: optionalCount(item, 'grant_days'),
    invite: invite === null ? null : { id: text(invite, 'id'), state: text(invite, 'state') },
    decidedBy: by === null ? null : { id: text(by, 'id'), email: optionalText(by, 'email') },
    decidedAt: optionalText(item, 'decided_at'),
    createdAt: text(item, 'created_at'),
  }
}

export interface RequestPage {
  requests: InviteRequest[]
  nextOffset: number | null
}

export async function listInviteRequests(
  state: RequestState,
  offset: number,
  limit: number,
  signal?: AbortSignal,
): Promise<RequestPage> {
  const path = `${BASE}?state=${state}&offset=${String(offset)}&limit=${String(limit)}`
  const body = object(await get(path, signal), 'invite requests')
  const next = body.next_offset
  return {
    requests: list(body, 'requests').map(decodeRequest),
    nextOffset: typeof next === 'number' ? next : null,
  }
}

export interface ApproveInput {
  /** null: a plain invite with no tier. */
  planId: string | null
  grantDays: number
  expiresDays: number
  sendEmail: boolean
}

export async function approveInviteRequest(id: string, input: ApproveInput): Promise<Issued> {
  const body: JsonObject = {
    expires_days: input.expiresDays,
    send_email: input.sendEmail,
  }
  if (input.planId !== null) {
    body.plan_id = input.planId
    body.grant_days = input.grantDays
  }
  const answer = object(await post(`${BASE}/${encodeURIComponent(id)}/approve`, body), 'issued')
  const email = answer.email
  return {
    code: text(answer, 'code'),
    request: decodeRequest(answer.request),
    email: email === 'queued' || email === 'mail_off' ? email : 'not_requested',
  }
}

export async function declineInviteRequest(id: string): Promise<InviteRequest> {
  return decodeRequest(await post(`${BASE}/${encodeURIComponent(id)}/decline`))
}

/** Every pending request (the queue is bounded by `PENDING_CAP`), so the screen can count them. */
export async function listAllPending(signal?: AbortSignal): Promise<InviteRequest[]> {
  const all: InviteRequest[] = []
  let offset: number | null = 0
  // The server caps an admin page at 100 and the queue at 1000: ten pages, plus one for safety.
  for (let round = 0; offset !== null && round < 11; round++) {
    const page: RequestPage = await listInviteRequests('pending', offset, 100, signal)
    all.push(...page.requests)
    offset = page.nextOffset
  }
  return all
}
