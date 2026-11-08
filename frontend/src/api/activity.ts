import { get } from './client'
import { flag, list, object, optionalText, text } from './decode'

/**
 * The kinds of security event the server lists (`activity.SHOWN_KINDS`, ADR 0003 amendment of
 * 2026-11-07, #678). A test holds this list equal to the backend's; a kind not in it is dropped.
 */
export const ACTIVITY_KINDS = [
  'sign_in',
  'sign_in_failed',
  'sign_out',
  'sign_out_all',
  'reauth',
  'reauth_failed',
  'signup_confirmed',
  'password_changed',
  'password_reset',
  'email_change_requested',
  'email_changed',
  'identity_linked',
  'identity_unlinked',
  'totp_enrolled',
  'totp_replaced',
  'totp_removed',
  'recovery_codes_regenerated',
  'mfa_failed',
  'mfa_reset',
  'session_location_changed',
  'passkey_added',
  'passkey_renamed',
  'passkey_removed',
  'passkey_counter_regressed',
] as const

export type ActivityKind = (typeof ACTIVITY_KINDS)[number]

function isActivityKind(value: string): value is ActivityKind {
  return (ACTIVITY_KINDS as readonly string[]).includes(value)
}

export interface ActivityEvent {
  id: string
  kind: ActivityKind
  at: string
  /** The address the event came from, as the data export holds it. */
  ip: string | null
  /** `google`, `github` or `microsoft` for a sign-in or re-authentication with a provider. */
  provider: string | null
  /** A sign-in or re-authentication with a passkey (`method: passkey`). */
  passkey: boolean
  /** A sign-out made from another of the user's sessions (the device list). */
  otherSession: boolean
}

export interface ActivityPage {
  events: ActivityEvent[]
  /** Pass to `listActivity` for the next, older page; null at the end. */
  nextBefore: string | null
}

/** `GET /api/auth/events`: the user's own security events, newest first, a page at a time. */
export async function listActivity(before: string | null, signal?: AbortSignal): Promise<ActivityPage> {
  const query = before === null ? '' : `?before=${encodeURIComponent(before)}`
  const body = object(await get(`/api/auth/events${query}`, signal), 'events')
  const events: ActivityEvent[] = []
  for (const item of list(body, 'events')) {
    const row = object(item, 'event')
    const kind = text(row, 'kind')
    if (!isActivityKind(kind)) continue
    events.push({
      id: text(row, 'id'),
      kind,
      at: text(row, 'at'),
      ip: optionalText(row, 'ip'),
      provider: optionalText(row, 'provider'),
      passkey: row.passkey === true,
      otherSession: flag(row, 'other_session'),
    })
  }
  return { events, nextBefore: optionalText(body, 'next_before') }
}
