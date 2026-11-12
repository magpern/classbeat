import { describe, expect, it } from 'vitest'
import type { Billing, Offer, Subscription } from '../api/billing'
import { canCancel, confirmKey, doneKey, offerKind, scheduled, stillOffered } from './billingChoice'

const sub = (extra: Partial<Subscription> = {}): Subscription => ({
  plan: 'instructor',
  provider: 'dummy',
  status: 'active',
  periodStart: '2026-11-15T00:00:00Z',
  periodEnd: '2026-12-15T00:00:00Z',
  nextPlan: null,
  cancelAtEnd: false,
  ...extra,
})

const offer = (extra: Partial<Offer> = {}): Offer => ({
  plan: 'pro',
  name: 'Professional',
  priceMinor: 2900,
  currency: 'USD',
  action: 'upgrade',
  chargeMinor: 1000,
  reason: null,
  ...extra,
})

describe('billingChoice', () => {
  it('knows when a change waits for the period end', () => {
    expect(scheduled(null)).toBe(false)
    expect(scheduled(sub())).toBe(false)
    expect(scheduled(sub({ cancelAtEnd: true }))).toBe(true)
    expect(scheduled(sub({ nextPlan: 'free' }))).toBe(true)
  })

  it('hides resume unless something is scheduled, and hides an offer with no action', () => {
    expect(offerKind(offer({ action: null }), sub())).toBeNull()
    expect(offerKind(offer({ action: 'resume' }), sub())).toBeNull()
    expect(offerKind(offer({ action: 'resume' }), sub({ cancelAtEnd: true }))).toBe('resume')
    expect(offerKind(offer(), sub())).toBe('upgrade')
  })

  it('lets only an active subscription without a cancel be cancelled', () => {
    expect(canCancel(null)).toBe(false)
    expect(canCancel(sub())).toBe(true)
    expect(canCancel(sub({ cancelAtEnd: true }))).toBe(false)
    expect(canCancel(sub({ status: 'past_due' }))).toBe(false)
  })

  it('checks a choice against a refreshed answer', () => {
    const b: Billing = { subscription: sub(), offers: [offer()], checkoutUrl: null }
    expect(stillOffered(b, { kind: 'upgrade', plan: 'pro' })).toBe(true)
    expect(stillOffered(b, { kind: 'buy', plan: 'pro' })).toBe(false)
    expect(stillOffered(b, { kind: 'upgrade', plan: 'instructor' })).toBe(false)
    expect(stillOffered(b, { kind: 'cancel', plan: 'free' })).toBe(true)
    expect(stillOffered({ ...b, subscription: null }, { kind: 'cancel', plan: 'free' })).toBe(false)
  })

  it('picks the confirm and done wording', () => {
    expect(confirmKey('upgrade', 0)).toBe('plan.confirm.upgradeFree')
    expect(confirmKey('upgrade', 5)).toBe('plan.confirm.upgrade')
    expect(confirmKey('buy', 900)).toBe('plan.confirm.buy')
    expect(confirmKey('cancel', 0)).toBe('plan.confirm.cancel')
    expect(doneKey('buy')).toBe('plan.done.now')
    expect(doneKey('resume')).toBe('plan.done.resume')
    expect(doneKey('downgrade')).toBe('plan.done.scheduled')
  })
})
