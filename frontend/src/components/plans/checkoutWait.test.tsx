import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { PLAN_FREE, SIGNED_IN, fakeApi } from '../../test/fakeApi'
import { renderAt } from '../../test/render'
import { forget, remember } from '../../lib/checkoutWait'

// PB-3 (#974, ADR 0032 section 7): a Paddle checkout completes when the signed notification lands. The
// buyer is sent to the hosted page, comes back to "Waiting for confirmation", and the plan is read until the
// payment lands. The clock runs fast here: the page reads every 2 s for 2 minutes.
const goToProvider = vi.hoisted(() => vi.fn())
vi.mock('../../lib/navigation', () => ({ goToProvider }))
vi.mock('../../lib/checkoutWait', async (original) => ({
  ...(await original<typeof import('../../lib/checkoutWait')>()),
  POLL_MS: 15,
  GIVE_UP_MS: 150,
}))

const ACCOUNT = {
  'GET /api/auth/session': SIGNED_IN,
  'GET /api/auth/identities': { identities: [], has_password: true, available: [] },
  'GET /api/auth/mfa': { totp_enabled: false, recovery_codes_left: 0 },
}

const offer = (plan: string, name: string, price: number, action: string, charge: number) => ({
  plan, name, price_minor: price, currency: 'USD', action, charge_minor: charge, reason: null,
})
const BUY = [offer('instructor', 'Individual', 900, 'buy', 900), offer('pro', 'Professional', 2900, 'buy', 2900)]
const subscription = (extra: Record<string, unknown> = {}) => ({
  plan: 'instructor', provider: 'fake_paddle', status: 'active',
  period_start: '2026-11-15T00:00:00Z', period_end: '2026-12-15T00:00:00Z',
  next_plan: null, cancel_at_end: false, ...extra,
})
const INDIVIDUAL = { ...PLAN_FREE, plan: 'instructor', name: 'Individual', source: 'subscription' }
const HOSTED = 'http://localhost:3000/fake-paddle/checkout/txn_1'

beforeEach(() => {
  goToProvider.mockReset()
})
afterEach(() => {
  forget()
})

describe('a buy that answers a hosted page', () => {
  it('sends the browser there and remembers what is paid for', async () => {
    const api = fakeApi({
      ...ACCOUNT,
      'GET /api/account/billing': { can_buy: true, subscription: null, offers: BUY },
      'POST /api/account/billing/checkout': { can_buy: true, subscription: null, offers: BUY, checkout_url: HOSTED },
    })
    renderAt('/account#plan')
    fireEvent.click(await screen.findByRole('button', { name: 'Buy Individual' }))
    fireEvent.click(within(await screen.findByTestId('offer-confirm')).getByRole('button', { name: 'Confirm' }))
    await screen.findByText('Taking you to the payment page.')
    expect(goToProvider).toHaveBeenCalledExactlyOnceWith(HOSTED)
    expect(api.to('POST', '/api/account/billing/checkout')).toHaveLength(1)
    expect(sessionStorage.getItem('classbeat.checkout.pending')).toBe('{"plan":"instructor","kind":"buy"}')
    expect(screen.queryByText(/Done\. Your plan/)).toBeNull() // nothing is bought until the notification lands
  })

  it('never goes to an address that is not on our own origin', async () => {
    fakeApi({
      ...ACCOUNT,
      'GET /api/account/billing': { can_buy: true, subscription: null, offers: BUY },
      'POST /api/account/billing/checkout': { can_buy: true, subscription: null, offers: BUY, checkout_url: 'https://pay.test/c/1' },
    })
    renderAt('/account#plan')
    fireEvent.click(await screen.findByRole('button', { name: 'Buy Individual' }))
    fireEvent.click(within(await screen.findByTestId('offer-confirm')).getByRole('button', { name: 'Confirm' }))
    await screen.findByTestId('checkout-waiting')
    expect(goToProvider).not.toHaveBeenCalled()
  })
})

describe('the return from the hosted page', () => {
  it('waits for confirmation, reads the plan until the webhook lands, then says so', async () => {
    remember({ plan: 'instructor', kind: 'buy' })
    let reads = 0
    const api = fakeApi({
      ...ACCOUNT,
      'GET /api/account/plan': () => (reads >= 3 ? INDIVIDUAL : PLAN_FREE),
      'GET /api/account/billing': () => {
        reads += 1
        return { can_buy: true, subscription: reads >= 3 ? subscription() : null, offers: BUY }
      },
    })
    renderAt('/account?checkout=return#plan')
    expect((await screen.findByTestId('checkout-waiting')).textContent).toContain('Waiting for confirmation')
    expect(screen.queryByText(/Done\. Your plan/)).toBeNull()
    await screen.findByText('Done. Your plan is now Individual.')
    expect(screen.queryByTestId('checkout-waiting')).toBeNull()
    expect(api.to('GET', '/api/account/billing').length).toBeGreaterThanOrEqual(3)
    expect(window.location.search).not.toContain('checkout=') // a reload does not wait again
    expect(sessionStorage.getItem('classbeat.checkout.pending')).toBeNull()
    // The done note shows at once; the plan line follows when the plan is read again (`onChanged`), so wait
    // for that read instead of asserting on the element the moment it exists.
    await waitFor(() => {
      expect(screen.getByTestId('plan-name').textContent).toContain('Your plan: Individual')
    })
  })

  it('stops after the limit with a calm "still waiting", and can look again', async () => {
    remember({ plan: 'instructor', kind: 'buy' })
    let landed = false
    const api = fakeApi({
      ...ACCOUNT,
      'GET /api/account/billing': () => ({
        can_buy: true, subscription: landed ? subscription() : null, offers: BUY,
      }),
    })
    renderAt('/account?checkout=return#plan')
    const slow = await screen.findByTestId('checkout-slow')
    expect(slow.textContent).toContain('Still waiting for the payment to be confirmed')
    expect(screen.queryByRole('alert')).toBeNull() // not an error
    const reads = api.to('GET', '/api/account/billing').length
    await new Promise((r) => setTimeout(r, 60))
    expect(api.to('GET', '/api/account/billing').length).toBe(reads) // it stopped asking
    landed = true
    fireEvent.click(within(slow).getByRole('button', { name: 'Check again' }))
    await screen.findByText('Done. Your plan is now Individual.')
  })

  it('shows a failed payment with its reason and leaves the plan unchanged', async () => {
    remember({ plan: 'instructor', kind: 'buy' })
    const api = fakeApi({
      ...ACCOUNT,
      'GET /api/account/billing': { can_buy: true, subscription: null, offers: BUY },
    })
    renderAt('/account?checkout=failed#plan')
    const failed = await screen.findByTestId('checkout-failed')
    expect(failed.textContent).toContain('The payment did not go through, so nothing was charged. Your plan is unchanged.')
    expect((await screen.findByTestId('plan-name')).textContent).toBe('Your plan: Free')
    expect(screen.queryByTestId('checkout-waiting')).toBeNull()
    expect(screen.queryByText(/Done\. Your plan/)).toBeNull()
    expect(api.calls.filter((c) => c.method === 'POST')).toEqual([])
    expect(sessionStorage.getItem('classbeat.checkout.pending')).toBeNull()
    fireEvent.click(within(failed).getByRole('button', { name: 'Close' }))
    expect(screen.queryByTestId('checkout-failed')).toBeNull()
  })

  it('ignores a return it has nothing remembered for', async () => {
    fakeApi({ ...ACCOUNT, 'GET /api/account/billing': { can_buy: true, subscription: null, offers: BUY } })
    renderAt('/account?checkout=return#plan')
    await screen.findByRole('button', { name: 'Buy Individual' })
    expect(screen.queryByTestId('checkout-waiting')).toBeNull()
  })
})

describe('an upgrade that only asks Paddle', () => {
  it('waits for the charge to land, without leaving the page', async () => {
    let reads = 0
    const current = subscription()
    const api = fakeApi({
      ...ACCOUNT,
      'GET /api/account/plan': INDIVIDUAL,
      'GET /api/account/billing': () => {
        reads += 1
        return { can_buy: true, subscription: reads >= 3 ? subscription({ plan: 'pro' }) : current, offers: [offer('pro', 'Professional', 2900, 'upgrade', 1234)] }
      },
      'POST /api/account/billing/checkout': { can_buy: true, subscription: current, offers: [offer('pro', 'Professional', 2900, 'upgrade', 1234)] },
    })
    renderAt('/account#plan')
    fireEvent.click(await screen.findByRole('button', { name: 'Upgrade to Professional' }))
    fireEvent.click(within(await screen.findByTestId('offer-confirm')).getByRole('button', { name: 'Confirm' }))
    await screen.findByTestId('checkout-waiting')
    await screen.findByText('Done. Your plan is now Professional.')
    expect(goToProvider).not.toHaveBeenCalled()
    expect(api.to('POST', '/api/account/billing/checkout')).toHaveLength(1)
    await waitFor(() => {
      expect(screen.queryByTestId('checkout-waiting')).toBeNull()
    })
  })
})
