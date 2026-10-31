import { ApiError } from './client'
import { isObject, type JsonObject } from './decode'
import { errorMessage } from './messages'

// Plain sentences for the plan refusals (ADR 0011 section 9): 409 `quota` with its kind, limit,
// used count, period end and plan; 429 `busy` with the plan's limit; and the redeem and trial
// refusals. Numbers come from the server's body and are checked; nothing from it is markup.

const MONTHS = [
  'January',
  'February',
  'March',
  'April',
  'May',
  'June',
  'July',
  'August',
  'September',
  'October',
  'November',
  'December',
]

function parse(iso: unknown): Date | null {
  if (typeof iso !== 'string') return null
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? null : date
}

/** "1 November": the day a period ends (the start of the next UTC month). */
export function dayText(iso: unknown): string | null {
  const date = parse(iso)
  return date === null ? null : `${String(date.getUTCDate())} ${MONTHS[date.getUTCMonth()]!}`
}

/** "October": the UTC month of a period that ends at `periodEnd`. */
export function periodMonth(periodEnd: unknown): string | null {
  const date = parse(periodEnd)
  return date === null ? null : MONTHS[new Date(date.getTime() - 1).getUTCMonth()]!
}

function detailOf(error: ApiError): JsonObject {
  const body: unknown = error.body
  if (!isObject(body)) return {}
  return isObject(body.detail) ? body.detail : {}
}

function whole(value: unknown): number | null {
  return typeof value === 'number' && Number.isInteger(value) && value >= 0 ? value : null
}

function classes(n: number): string {
  return `${String(n)} ${n === 1 ? 'class' : 'classes'}`
}

/**
 * The sentence for a 409 `quota`. The kind (`classes` or `cost`) is the body's `quota` field,
 * defaulting to `classes`; `cost` means the month's model budget is used up.
 */
export function quotaText(error: ApiError): string {
  const detail = detailOf(error)
  const month = periodMonth(detail.period_end) ?? 'this month'
  const day = dayText(detail.period_end)
  const more = day === null ? 'More at the start of next month.' : `More from ${day}.`
  if (detail.quota === 'cost') {
    return `You have used what your plan allows for ${month}, so no more classes can be designed for now. ${more}`
  }
  const limit = whole(detail.limit)
  if (limit === null) return `You have used your classes for ${month}. ${more}`
  const used = whole(detail.used)
  return used !== null && used !== limit
    ? `You have used ${String(used)} of your ${classes(limit)} for ${month}. ${more}`
    : `You have used your ${classes(limit)} for ${month}. ${more}`
}

/** The sentence for a 429 `busy`, with the plan's limit when the server sent it. */
export function busyText(error: ApiError): string {
  const limit = whole(detailOf(error).limit)
  if (limit === null || limit < 1) {
    return 'You already have designs running. Wait for one to finish, then try again.'
  }
  return `Your plan allows ${String(limit)} ${limit === 1 ? 'design' : 'designs'} at a time, and that many are running. Wait for one to finish, then try again.`
}

const ACCOUNT_REASONS: Record<string, string> = {
  code_invalid: 'That code is not valid. Check it and try again. It may have expired or been used up.',
  already_redeemed: 'You have already used that code.',
  trial_used: 'You have already had your trial.',
  trial_off: 'Trials are not available right now.',
}

/** The sentence for a plan refusal, or null when the error is not one of them. */
export function planRefusalText(error: unknown): string | null {
  if (!(error instanceof ApiError)) return null
  if (error.reason === 'quota') return quotaText(error)
  if (error.reason === 'busy') return busyText(error)
  if (error.reason !== null && Object.hasOwn(ACCOUNT_REASONS, error.reason)) {
    return ACCOUNT_REASONS[error.reason]!
  }
  return null
}

/** The words for a failed redeem or trial request on the account page. */
export function accountPlanError(error: unknown): string {
  return planRefusalText(error) ?? errorMessage(error)
}

const SOURCES: Record<string, string> = {
  grant: 'granted to you',
  code: 'from a code',
  trial: 'your trial',
  subscription: 'a subscription',
}

export function sourceText(source: string | null): string | null {
  return source !== null && Object.hasOwn(SOURCES, source) ? SOURCES[source]! : null
}
