import { ApiError, get, post, request } from './client'
import {
  count,
  flag,
  isObject,
  list,
  object,
  optionalText,
  strings,
  text,
  type JsonObject,
} from './decode'
import { errorMessage } from './messages'

// The admin area's API (ADR 0011 section 6): Users (backend/src/classbeat/admin/users.py, #218)
// and Codes (backend/src/classbeat/admin/codes.py, #220). Every route is behind the admin gate;
// writes need a step-up (403 `stepup_required`). Server text is data, rendered as text only.
// A created invite or redeem code is returned to the caller once and never kept here.

const BASE = '/api/admin'

// --- users -------------------------------------------------------------------------------------

export interface PlanSummary {
  id: string
  name: string
  source: string | null
  endsAt: string | null
}

export interface AdminUser {
  id: string
  email: string
  createdAt: string
  role: string
  disabledAt: string | null
  plan: PlanSummary
  features: string[]
  classesHeld: number
  classesLimit: number
  costMicros: number
}

export interface UserPage {
  users: AdminUser[]
  nextOffset: number | null
}

export interface Entitlement {
  id: string
  planId: string | null
  features: string[]
  source: string
  startsAt: string
  endsAt: string | null
  note: string | null
  revokedAt: string | null
  createdAt: string
  active: boolean
}

export interface LedgerRow {
  id: string
  period: string
  planId: string
  state: string
  reason: string | null
  settledAt: string | null
  createdAt: string
}

export interface ConnectionStatus {
  provider: string
  status: string
  lastError: string | null
  connectedAt: string | null
  lastUsedAt: string | null
}

export interface UserDetail extends AdminUser {
  entitlements: Entitlement[]
  ledger: LedgerRow[]
  connections: ConnectionStatus[]
}

function nextOffset(body: JsonObject): number | null {
  const value = body.next_offset
  return typeof value === 'number' ? value : null
}

function decodeUser(value: unknown): AdminUser {
  const item = object(value, 'user')
  const plan = object(item.plan, 'plan')
  return {
    id: text(item, 'id'),
    email: text(item, 'email'),
    createdAt: text(item, 'created_at'),
    role: text(item, 'role'),
    disabledAt: optionalText(item, 'disabled_at'),
    plan: {
      id: text(plan, 'id'),
      name: text(plan, 'name'),
      source: optionalText(plan, 'source'),
      endsAt: optionalText(plan, 'ends_at'),
    },
    features: strings(item, 'features'),
    classesHeld: count(item, 'classes_held'),
    classesLimit: count(item, 'classes_limit'),
    costMicros: count(item, 'cost_micros'),
  }
}

export function decodeEntitlement(value: unknown): Entitlement {
  const item = object(value, 'entitlement')
  return {
    id: text(item, 'id'),
    planId: optionalText(item, 'plan_id'),
    features: strings(item, 'features'),
    source: text(item, 'source'),
    startsAt: text(item, 'starts_at'),
    endsAt: optionalText(item, 'ends_at'),
    note: optionalText(item, 'note'),
    revokedAt: optionalText(item, 'revoked_at'),
    createdAt: text(item, 'created_at'),
    active: flag(item, 'active'),
  }
}

function decodeDetail(value: unknown): UserDetail {
  const item = object(value, 'user')
  return {
    ...decodeUser(item),
    entitlements: list(item, 'entitlements').map(decodeEntitlement),
    ledger: list(item, 'ledger').map((row) => {
      const entry = object(row, 'ledger')
      return {
        id: text(entry, 'id'),
        period: text(entry, 'period'),
        planId: text(entry, 'plan_id'),
        state: text(entry, 'state'),
        reason: optionalText(entry, 'reason'),
        settledAt: optionalText(entry, 'settled_at'),
        createdAt: text(entry, 'created_at'),
      }
    }),
    connections: list(item, 'connections').map((row) => {
      const entry = object(row, 'connection')
      return {
        provider: text(entry, 'provider'),
        status: text(entry, 'status'),
        lastError: optionalText(entry, 'last_error'),
        connectedAt: optionalText(entry, 'connected_at'),
        lastUsedAt: optionalText(entry, 'last_used_at'),
      }
    }),
  }
}

export const PAGE_SIZE = 50

/** The users list path: an email prefix (only in this API request, never the page's address). */
export function usersPath(prefix: string, offset: number): string {
  const params = new URLSearchParams()
  if (prefix.trim() !== '') params.set('q', prefix.trim())
  params.set('offset', String(offset))
  params.set('limit', String(PAGE_SIZE))
  return `${BASE}/users?${params.toString()}`
}

export async function listUsers(prefix: string, offset: number, signal?: AbortSignal): Promise<UserPage> {
  const body = object(await get(usersPath(prefix, offset), signal), 'users')
  return { users: list(body, 'users').map(decodeUser), nextOffset: nextOffset(body) }
}

function userPath(id: string): string {
  return `${BASE}/users/${encodeURIComponent(id)}`
}

export async function getUser(id: string, signal?: AbortSignal): Promise<UserDetail> {
  return decodeDetail(await get(userPath(id), signal))
}

export interface GrantInput {
  planId: string | null
  features: string[]
  /** An instant with its time zone (ISO 8601), or null for no end. */
  endsAt: string | null
  note: string | null
}

export async function grantEntitlement(id: string, input: GrantInput): Promise<Entitlement> {
  const body: JsonObject = { features: input.features }
  if (input.planId !== null) body.plan_id = input.planId
  if (input.endsAt !== null) body.ends_at = input.endsAt
  if (input.note !== null) body.note = input.note
  return decodeEntitlement(await post(`${userPath(id)}/entitlements`, body))
}

export async function revokeEntitlement(id: string, entitlementId: string): Promise<Entitlement> {
  return decodeEntitlement(
    await post(`${userPath(id)}/entitlements/${encodeURIComponent(entitlementId)}/revoke`),
  )
}

export async function disableUser(id: string): Promise<void> {
  await post(`${userPath(id)}/disable`)
}

export async function enableUser(id: string): Promise<void> {
  await post(`${userPath(id)}/enable`)
}

/** Deletes the account. The typed email goes in the request body only. */
export async function deleteUser(id: string, typedEmail: string): Promise<void> {
  await request('DELETE', userPath(id), { body: { email: typedEmail } })
}

// --- codes -------------------------------------------------------------------------------------

export interface Person {
  id: string
  /** null when the account is gone. */
  email: string | null
}

export interface Invite {
  id: string
  createdBy: Person | null
  usedBy: Person | null
  createdAt: string
  expiresAt: string
  usedAt: string | null
  state: string
}

export interface RedeemCode {
  id: string
  label: string
  planId: string | null
  features: string[]
  grantDays: number
  maxRedemptions: number
  redeemedCount: number
  expiresAt: string
  disabledAt: string | null
  createdBy: Person | null
  createdAt: string
  state: string
}

/** A create response: the code is shown once by the caller and then dropped. */
export interface Created<T> {
  code: string
  item: T
}

function decodePerson(value: unknown): Person | null {
  if (value === null || value === undefined) return null
  const item = object(value, 'person')
  return { id: text(item, 'id'), email: optionalText(item, 'email') }
}

function decodeInvite(value: unknown): Invite {
  const item = object(value, 'invite')
  return {
    id: text(item, 'id'),
    createdBy: decodePerson(item.created_by),
    usedBy: decodePerson(item.used_by),
    createdAt: text(item, 'created_at'),
    expiresAt: text(item, 'expires_at'),
    usedAt: optionalText(item, 'used_at'),
    state: text(item, 'state'),
  }
}

function decodeCode(value: unknown): RedeemCode {
  const item = object(value, 'redeem code')
  return {
    id: text(item, 'id'),
    label: text(item, 'label'),
    planId: optionalText(item, 'plan_id'),
    features: strings(item, 'features'),
    grantDays: count(item, 'grant_days'),
    maxRedemptions: count(item, 'max_redemptions'),
    redeemedCount: count(item, 'redeemed_count'),
    expiresAt: text(item, 'expires_at'),
    disabledAt: optionalText(item, 'disabled_at'),
    createdBy: decodePerson(item.created_by),
    createdAt: text(item, 'created_at'),
    state: text(item, 'state'),
  }
}

function created<T>(value: unknown, key: string, decode: (item: unknown) => T): Created<T> {
  const body = object(value, 'created')
  const code = body.code
  if (typeof code !== 'string' || code === '' || !isObject(body[key])) throw new ApiError(500, 'bad_response', null, undefined)
  return { code, item: decode(body[key]) }
}

export function invitesPath(offset: number): string {
  return `${BASE}/invites?offset=${String(offset)}&limit=${String(PAGE_SIZE)}`
}

export function codesPath(offset: number): string {
  return `${BASE}/redeem-codes?offset=${String(offset)}&limit=${String(PAGE_SIZE)}`
}

export async function listInvites(
  offset: number,
  signal?: AbortSignal,
): Promise<{ invites: Invite[]; nextOffset: number | null }> {
  const body = object(await get(invitesPath(offset), signal), 'invites')
  return { invites: list(body, 'invites').map(decodeInvite), nextOffset: nextOffset(body) }
}

export async function createInvite(expiresDays: number): Promise<Created<Invite>> {
  return created(await post(`${BASE}/invites`, { expires_days: expiresDays }), 'invite', decodeInvite)
}

export async function disableInvite(id: string): Promise<Invite> {
  return decodeInvite(await post(`${BASE}/invites/${encodeURIComponent(id)}/disable`))
}

export async function listCodes(
  offset: number,
  signal?: AbortSignal,
): Promise<{ codes: RedeemCode[]; nextOffset: number | null }> {
  const body = object(await get(codesPath(offset), signal), 'redeem codes')
  return { codes: list(body, 'redeem_codes').map(decodeCode), nextOffset: nextOffset(body) }
}

export interface CodeInput {
  label: string
  planId: string | null
  features: string[]
  grantDays: number
  maxRedemptions: number
  expiresAt: string
}

export async function createCode(input: CodeInput): Promise<Created<RedeemCode>> {
  const body: JsonObject = {
    label: input.label,
    features: input.features,
    grant_days: input.grantDays,
    max_redemptions: input.maxRedemptions,
    expires_at: input.expiresAt,
  }
  if (input.planId !== null) body.plan_id = input.planId
  return created(await post(`${BASE}/redeem-codes`, body), 'redeem_code', decodeCode)
}

export async function disableCode(id: string): Promise<RedeemCode> {
  return decodeCode(await post(`${BASE}/redeem-codes/${encodeURIComponent(id)}/disable`))
}

// --- wording -----------------------------------------------------------------------------------

const ADMIN_REASONS: Record<string, string> = {
  forbidden: 'Your account is not an admin account.',
  not_found: 'That no longer exists. Reload the list.',
  target_is_admin: 'Admins cannot be disabled or deleted here. Use the command line.',
  email_mismatch: 'The email you typed does not match this account.',
  already_revoked: 'That entitlement was already revoked.',
  unknown_plan: 'There is no such plan.',
  ends_in_past: 'The end date must be in the future.',
  expires_in_past: 'The expiry must be in the future.',
  invite_used: 'That invite has been used already.',
  already_expired: 'That invite has expired already.',
  already_disabled: 'That code is disabled already.',
  admin_unavailable: 'The admin area is not available right now.',
  stepup_required: 'This change needs a two-factor code.',
  mfa_required: 'Confirm your two-factor code to continue.',
  invalid_code: 'That code is not right. Try again.',
}

/** The text for an admin API error. */
export function adminError(error: unknown): string {
  if (error instanceof ApiError && error.reason !== null && Object.hasOwn(ADMIN_REASONS, error.reason)) {
    return ADMIN_REASONS[error.reason] ?? errorMessage(error)
  }
  return errorMessage(error)
}

/** Micro-dollars as dollars and cents (the admin sees costs; users never do). */
export function dollars(micros: number): string {
  return `$${(micros / 1_000_000).toFixed(2)}`
}
