import type { Billing } from '../api/billing'

// The deferred checkout of a Paddle provider (ADR 0032 section 7): the buyer pays on a hosted page, and the
// plan changes only when the signed notification reaches the server. This file holds the rules of the wait:
// what is remembered across the trip, how the return is read, when the plan has landed and how long we
// look. No network and no timer here; `useCheckoutWait` runs them.

/** How often the plan is read while we wait, and when we stop and say it is slow. */
export const POLL_MS = 2000
export const GIVE_UP_MS = 120_000

const KEY = 'classbeat.checkout.pending'
const PARAM = 'checkout'

/** What the page remembers while the buyer is away: the plan being paid for and what it would be called. */
export interface Pending {
  plan: string
  kind: 'buy' | 'upgrade'
}

export type Return = 'return' | 'failed'

/** The hosted page sends the buyer back to Account with `?checkout=return` (paid) or `?checkout=failed`. */
export function returnOf(search: string): Return | null {
  const value = new URLSearchParams(search).get(PARAM)
  return value === 'return' || value === 'failed' ? value : null
}

/** The search part without the `checkout` parameter (`''` or `?a=1`), so a reload does not start the wait again. */
export function withoutReturn(search: string): string {
  const params = new URLSearchParams(search)
  params.delete(PARAM)
  const rest = params.toString()
  return rest === '' ? '' : `?${rest}`
}

/**
 * Where to send the browser: the server's address when it is http(s), else null. The server's text is data,
 * and `javascript:` or `data:` must never be navigated to.
 */
export function safeCheckoutUrl(value: string | null, base: string): string | null {
  if (value === null) return null
  try {
    const url = new URL(value, base)
    return url.protocol === 'https:' || url.protocol === 'http:' ? url.href : null
  } catch {
    return null
  }
}

/** Whether the payment has landed: an active subscription of that plan on a Paddle provider. */
export function landed(billing: Billing, pending: Pending): boolean {
  const sub = billing.subscription
  return sub !== null && sub.status === 'active' && sub.plan === pending.plan && sub.provider !== 'dummy'
}

/**
 * Whether a buy or an upgrade still waits for Paddle after the server answered: Dummy completes at once
 * (the plan is already the chosen one), a Paddle provider answers a hosted page or, for an upgrade, only asks Paddle.
 */
export function deferred(billing: Billing, pending: Pending): boolean {
  const sub = billing.subscription
  return !(sub !== null && sub.status === 'active' && sub.plan === pending.plan && sub.provider === 'dummy')
}

export function remember(pending: Pending): void {
  try {
    sessionStorage.setItem(KEY, JSON.stringify(pending))
  } catch {
    // Storage may be blocked: the buyer then sees no waiting state on return, and the plan still updates.
  }
}

export function recall(): Pending | null {
  try {
    const raw = sessionStorage.getItem(KEY)
    if (raw === null) return null
    const found: unknown = JSON.parse(raw)
    if (typeof found !== 'object' || found === null) return null
    const { plan, kind } = found as Record<string, unknown>
    return typeof plan === 'string' && (kind === 'buy' || kind === 'upgrade') ? { plan, kind } : null
  } catch {
    return null
  }
}

export function forget(): void {
  try {
    sessionStorage.removeItem(KEY)
  } catch {
    // Nothing to clear when storage is blocked.
  }
}
