import { afterEach, describe, expect, it } from 'vitest'
import type { Billing, Subscription } from '../api/billing'
import {
  deferred,
  forget,
  GIVE_UP_MS,
  landed,
  POLL_MS,
  recall,
  remember,
  returnOf,
  safeCheckoutUrl,
  withoutReturn,
} from './checkoutWait'

const sub = (extra: Partial<Subscription> = {}): Subscription => ({
  plan: 'instructor',
  provider: 'fake_paddle',
  status: 'active',
  periodStart: '2026-11-15T00:00:00Z',
  periodEnd: '2026-12-15T00:00:00Z',
  nextPlan: null,
  cancelAtEnd: false,
  ...extra,
})
const billing = (subscription: Subscription | null): Billing => ({ subscription, offers: [], checkoutUrl: null })
const PENDING = { plan: 'instructor', kind: 'buy' } as const

afterEach(() => {
  forget()
})

describe('the return address', () => {
  it('reads only the two values the hosted page sends', () => {
    expect(returnOf('?checkout=return')).toBe('return')
    expect(returnOf('?a=1&checkout=failed')).toBe('failed')
    expect(returnOf('?checkout=other')).toBeNull()
    expect(returnOf('')).toBeNull()
  })

  it('drops the parameter and keeps the rest', () => {
    expect(withoutReturn('?checkout=return&a=1')).toBe('?a=1')
    expect(withoutReturn('?checkout=failed')).toBe('')
    expect(withoutReturn('')).toBe('')
  })
})

describe('where the browser may be sent', () => {
  const base = 'http://x.test/en/account'

  it('follows an address on our own origin', () => {
    expect(safeCheckoutUrl('/fake-paddle/checkout/txn_1', base)).toBe('http://x.test/fake-paddle/checkout/txn_1')
    expect(safeCheckoutUrl('http://x.test/fake-paddle/checkout/txn_1', base)).toBe(
      'http://x.test/fake-paddle/checkout/txn_1',
    )
  })

  it('refuses another host, however it is written', () => {
    expect(safeCheckoutUrl('https://pay.test/c/1', base)).toBeNull()
    expect(safeCheckoutUrl('//evil.test/c/1', base)).toBeNull()
    expect(safeCheckoutUrl('http://evil.test/c/1', base)).toBeNull()
    expect(safeCheckoutUrl('http://x.test.evil.test/c/1', base)).toBeNull()
    expect(safeCheckoutUrl('http://x.test@evil.test/c/1', base)).toBeNull()
  })

  it('refuses another scheme or port on our own host', () => {
    expect(safeCheckoutUrl('https://x.test/c/1', base)).toBeNull()
    expect(safeCheckoutUrl('http://x.test:8080/c/1', base)).toBeNull()
  })

  it('refuses scripts, data and nothing', () => {
    expect(safeCheckoutUrl('javascript:alert(1)', base)).toBeNull()
    expect(safeCheckoutUrl('data:text/html,hi', base)).toBeNull()
    expect(safeCheckoutUrl(null, base)).toBeNull()
  })
})

describe('how long the wait lasts', () => {
  it('looks every 2 seconds and gives up after 2 minutes', () => {
    expect(POLL_MS).toBe(2_000)
    expect(GIVE_UP_MS).toBe(120_000)
  })
})

describe('when the payment has landed', () => {
  it('needs an active subscription of the plan on a Paddle provider', () => {
    expect(landed(billing(sub()), PENDING)).toBe(true)
    expect(landed(billing(null), PENDING)).toBe(false)
    expect(landed(billing(sub({ plan: 'pro' })), PENDING)).toBe(false)
    expect(landed(billing(sub({ status: 'ended' })), PENDING)).toBe(false)
  })

  it('is not met by a Dummy subscription of the same plan that the purchase replaces', () => {
    expect(landed(billing(sub({ provider: 'dummy' })), PENDING)).toBe(false)
  })

  it('knows a Dummy purchase is not deferred', () => {
    expect(deferred(billing(sub({ provider: 'dummy' })), PENDING)).toBe(false)
    expect(deferred(billing(null), PENDING)).toBe(true)
    expect(deferred(billing(sub({ plan: 'instructor' })), { plan: 'pro', kind: 'upgrade' })).toBe(true)
  })
})

describe('what is remembered across the trip', () => {
  it('round-trips and forgets', () => {
    expect(recall()).toBeNull()
    remember(PENDING)
    expect(recall()).toEqual(PENDING)
    forget()
    expect(recall()).toBeNull()
  })

  it('ignores a value it did not write', () => {
    sessionStorage.setItem('classbeat.checkout.pending', '{"plan":1}')
    expect(recall()).toBeNull()
    sessionStorage.setItem('classbeat.checkout.pending', 'not json')
    expect(recall()).toBeNull()
  })
})
