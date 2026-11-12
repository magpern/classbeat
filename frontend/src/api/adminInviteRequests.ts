import { get, post } from './client'
import { count, flag, list, object, optionalText, strings, text, optionalCount, type JsonObject } from './decode'

// The admin Invite requests screen (ADR 0033; backend/src/classbeat/admin/invite_requests.py,
// PB-7 #985). Every route is behind the admin gate and the POSTs need a step-up. `heard_from` is
// free text from a stranger: it is only ever rendered as text. The approve answer carries the
// invite code once; the caller shows it and drops it. The list counts the whole pending queue
// (`pending`), so the screen reads one page at a time (#989).

const BASE = '/api/admin/invite-requests'

/** The pending queue's bound on the server (`PENDING_CAP`, invite_request_policy.py). */
export const PENDING_CAP = 1000

/** The bulk decline's bound on the server (`DECLINE_MAX`, invite_request_models.py). */
export const DECLINE_MAX = 100

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
  /** The whole pending queue, counted by the server whatever the page and filter. */
  pending: number
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
    pending: count(body, 'pending'),
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

export interface Declined {
  declined: string[]
  /** Gone, or decided meanwhile. */
  skipped: string[]
}

/** Declines up to `DECLINE_MAX` requests at once (one audited transaction each on the server). */
export async function declineInviteRequests(ids: string[]): Promise<Declined> {
  const answer = object(await post(`${BASE}/decline`, { ids }), 'declined')
  return { declined: strings(answer, 'declined'), skipped: strings(answer, 'skipped') }
}
