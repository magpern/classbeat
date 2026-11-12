import { ApiError, get, post } from './client'
import { flag, list, object, optionalCount, optionalText, text } from './decode'

// The user's billing API (ADR 0031 sections 3 and 6, backend/src/classbeat/billing/routes.py). Only a
// tester or an admin gets an answer; everyone else gets 403 `billing_closed` and sees no price.
// Amounts are whole numbers in the currency's minor unit. Server text is data, rendered as text only.

export type OfferAction = 'buy' | 'upgrade' | 'downgrade' | 'resume'

export interface Subscription {
  plan: string
  /** Who bills it: `dummy`, or a Paddle provider whose signed notification starts and changes it (ADR 0032). */
  provider: string
  status: string
  periodStart: string
  periodEnd: string
  /** The plan a scheduled downgrade moves to at the period end. */
  nextPlan: string | null
  /** A scheduled cancel: the account returns to Free at the period end. */
  cancelAtEnd: boolean
}

export interface Offer {
  plan: string
  name: string
  priceMinor: number | null
  currency: string | null
  /** What choosing this plan does; null when it cannot be chosen (see `reason`). */
  action: OfferAction | null
  /** What choosing it charges now (0 for a downgrade or a resume). */
  chargeMinor: number | null
  reason: string | null
}

export interface Billing {
  subscription: Subscription | null
  offers: Offer[]
  /** A Paddle buy: the hosted page to pay on. The plan changes when the signed notification lands. */
  checkoutUrl: string | null
}

const ACTIONS: readonly string[] = ['buy', 'upgrade', 'downgrade', 'resume']

function decodeOffer(value: unknown): Offer {
  const item = object(value, 'offer')
  const action = optionalText(item, 'action')
  return {
    plan: text(item, 'plan'),
    name: text(item, 'name'),
    priceMinor: optionalCount(item, 'price_minor'),
    currency: optionalText(item, 'currency'),
    action: action !== null && ACTIONS.includes(action) ? (action as OfferAction) : null,
    chargeMinor: optionalCount(item, 'charge_minor'),
    reason: optionalText(item, 'reason'),
  }
}

function decodeSubscription(value: unknown): Subscription {
  const item = object(value, 'subscription')
  return {
    plan: text(item, 'plan'),
    provider: text(item, 'provider'),
    status: text(item, 'status'),
    periodStart: text(item, 'period_start'),
    periodEnd: text(item, 'period_end'),
    nextPlan: optionalText(item, 'next_plan'),
    cancelAtEnd: flag(item, 'cancel_at_end'),
  }
}

export function decodeBilling(value: unknown): Billing {
  const body = object(value, 'billing')
  const sub = body.subscription
  return {
    subscription: sub === null || sub === undefined ? null : decodeSubscription(sub),
    offers: list(body, 'offers').map(decodeOffer),
    checkoutUrl: optionalText(body, 'checkout_url'),
  }
}

/** The caller's billing view, or null when they may not buy (403 `billing_closed`): no price, no buy action. */
export async function getBilling(signal?: AbortSignal): Promise<Billing | null> {
  try {
    return decodeBilling(await get('/api/account/billing', signal))
  } catch (error) {
    if (error instanceof ApiError && error.status === 403 && error.reason === 'billing_closed') return null
    throw error
  }
}

/** A buy or an upgrade. `expectedAmountMinor` is the quote the page showed; 409 `quote_changed` if it moved. */
export async function checkout(plan: string, expectedAmountMinor: number, key: string): Promise<Billing> {
  const body = { plan, expected_amount_minor: expectedAmountMinor }
  return decodeBilling(await post('/api/account/billing/checkout', body, { 'Idempotency-Key': key }))
}

/** A downgrade, a cancel (`free`) or a resume (the current plan), all at the period end. */
export async function changePlan(plan: string, key: string): Promise<Billing> {
  return decodeBilling(await post('/api/account/billing/change', { plan }, { 'Idempotency-Key': key }))
}

