import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { PLAN_FREE, SIGNED_IN, fakeApi, json, reason } from '../../test/fakeApi'
import { renderAt } from '../../test/render'

// M7a-5 (#950): the plans on Account, Plan. A caller who may not buy sees what each plan includes and
// "Plans are not open yet"; a tester or an admin also sees the server's prices and quotes and confirms
// with the quote as the expected amount.

const ACCOUNT = {
  'GET /api/auth/session': SIGNED_IN,
  'GET /api/auth/identities': { identities: [], has_password: true, available: [] },
  'GET /api/auth/mfa': { totp_enabled: false, recovery_codes_left: 0 },
}

const offer = (plan: string, name: string, price: number, action: string | null, charge: number | null) => ({
  plan,
  name,
  price_minor: price,
  currency: 'USD',
  action,
  charge_minor: charge,
  reason: null,
})

function billing(subscription: unknown, offers: unknown[]) {
  return { can_buy: true, subscription, offers }
}

const NONE = billing(null, [
  offer('instructor', 'Individual', 900, 'buy', 900),
  offer('pro', 'Professional', 2900, 'buy', 2900),
])

const subscription = (extra: Record<string, unknown> = {}) => ({
  plan: 'instructor',
  status: 'active',
  period_start: '2026-11-15T00:00:00Z',
  period_end: '2026-12-15T00:00:00Z',
  next_plan: null,
  cancel_at_end: false,
  ...extra,
})

const INDIVIDUAL = { ...PLAN_FREE, plan: 'instructor', name: 'Individual', source: 'subscription' }

async function openPlan() {
  renderAt('/account#plan')
  return screen.findByTestId('plan-options')
}

describe('a caller who may not buy', () => {
  it('sees the plans, "Plans are not open yet", no price and no buy action', async () => {
    const api = fakeApi({ ...ACCOUNT })
    const options = await openPlan()
    await screen.findByText('Plans are not open yet. Here is what each one includes.')
    for (const name of ['Free', 'Individual', 'Professional']) {
      expect(within(options).getByRole('heading', { name })).toBeTruthy()
    }
    expect(within(options).getByTestId('plan-card-instructor').textContent).toContain('75 minutes')
    expect(options.textContent).not.toMatch(/\$|USD|price|test payment/i)
    expect(within(options).queryByRole('button')).toBeNull()
    expect(api.to('GET', '/api/account/billing')).toHaveLength(1)
    expect(api.calls.filter((c) => c.method === 'POST')).toEqual([])
  })

  it('sees the same when the billing answer fails', async () => {
    fakeApi({ ...ACCOUNT, 'GET /api/account/billing': () => reason(503, 'billing_unavailable') })
    const options = await openPlan()
    await screen.findByText('The plan options could not be loaded.')
    expect(options.textContent).not.toMatch(/\$/)
    expect(within(options).queryByRole('button')).toBeNull()
  })

  it('shows the source of an admin account', async () => {
    fakeApi({ ...ACCOUNT, 'GET /api/account/plan': { ...PLAN_FREE, plan: 'pro', name: 'Professional', source: 'admin' } })
    renderAt('/account#plan')
    expect((await screen.findByTestId('plan-name')).textContent).toBe('Your plan: Professional (administrator access)')
  })
})

describe('a tester or an admin', () => {
  it('sees the prices from the API and that these are test payments', async () => {
    fakeApi({ ...ACCOUNT, 'GET /api/account/billing': NONE })
    const options = await openPlan()
    await within(options).findByText('$9.00 a month')
    expect(within(options).getByText('$29.00 a month')).toBeTruthy()
    expect(within(options).getByText(/Test payments only/).textContent).toContain('Dummy')
    expect(within(options).getByRole('button', { name: 'Buy Individual' })).toBeTruthy()
    expect(within(options).getByRole('button', { name: 'Buy Professional' })).toBeTruthy()
    expect(within(options).queryByText('Plans are not open yet. Here is what each one includes.')).toBeNull()
  })

  it('buys: the confirm sends the quote as the expected amount, with an idempotency key', async () => {
    let bought = false
    const api = fakeApi({
      ...ACCOUNT,
      'GET /api/account/plan': () => (bought ? INDIVIDUAL : PLAN_FREE),
      'GET /api/account/billing': () => (bought ? billing(subscription(), []) : NONE),
      'POST /api/account/billing/checkout': () => {
        bought = true
        return billing(subscription(), [offer('pro', 'Professional', 2900, 'upgrade', 1450)])
      },
    })
    const options = await openPlan()
    fireEvent.click(await within(options).findByRole('button', { name: 'Buy Individual' }))
    const confirm = await screen.findByTestId('offer-confirm')
    expect(within(confirm).getByTestId('confirm-text').textContent).toBe(
      'Pay $9.00 now for one month of Individual. It renews every month until you cancel.',
    )
    expect(confirm.textContent).toContain('test payment (Dummy)')
    fireEvent.click(within(confirm).getByRole('button', { name: 'Confirm' }))
    await screen.findByText('Done. Your plan is now Individual.')
    const [call] = api.to('POST', '/api/account/billing/checkout')
    expect(call?.body).toEqual({ plan: 'instructor', expected_amount_minor: 900 })
    expect(call?.headers.get('Idempotency-Key')).toMatch(/^[A-Za-z0-9_-]{16,64}$/)
    expect(call?.headers.get('X-CSRF-Token')).toBeTruthy()
    expect((await screen.findByTestId('plan-subscription')).textContent).toBe('Subscription: Individual, renewing on 15 December.')
    expect(screen.queryByTestId('offer-confirm')).toBeNull()
  })

  it('upgrades: shows the prorated quote, that the period end stays, and sends the quote', async () => {
    const api = fakeApi({
      ...ACCOUNT,
      'GET /api/account/plan': INDIVIDUAL,
      'GET /api/account/billing': billing(subscription(), [
        offer('instructor', 'Individual', 900, 'resume', 0),
        offer('pro', 'Professional', 2900, 'upgrade', 1234),
      ]),
      'POST /api/account/billing/checkout': billing(subscription({ plan: 'pro' }), []),
    })
    const options = await openPlan()
    expect((await within(options).findByTestId('quote-pro')).textContent).toBe(
      'Upgrading now costs $12.34, for the rest of this period.',
    )
    // The current plan with nothing scheduled has no action of its own.
    expect(within(options).queryByRole('button', { name: /Keep/ })).toBeNull()
    fireEvent.click(within(options).getByRole('button', { name: 'Upgrade to Professional' }))
    const confirm = await screen.findByTestId('offer-confirm')
    expect(within(confirm).getByTestId('confirm-text').textContent).toBe(
      'Pay $12.34 now to move to Professional: the difference for the rest of this period. Your period still ends on 15 December.',
    )
    fireEvent.click(within(confirm).getByRole('button', { name: 'Confirm' }))
    await screen.findByText('Done. Your plan is now Professional.')
    expect(api.to('POST', '/api/account/billing/checkout')[0]?.body).toEqual({ plan: 'pro', expected_amount_minor: 1234 })
  })

  it('refreshes the quote on 409 quote_changed and asks again with the new amount and a new key', async () => {
    let amount = 1234
    const api = fakeApi({
      ...ACCOUNT,
      'GET /api/account/plan': INDIVIDUAL,
      'GET /api/account/billing': () =>
        billing(subscription(), [offer('instructor', 'Individual', 900, 'resume', 0), offer('pro', 'Professional', 2900, 'upgrade', amount)]),
      'POST /api/account/billing/checkout': (body) => {
        if ((body as { expected_amount_minor: number }).expected_amount_minor !== 1500) {
          amount = 1500
          return reason(409, 'quote_changed')
        }
        return billing(subscription({ plan: 'pro' }), [])
      },
    })
    const options = await openPlan()
    fireEvent.click(await within(options).findByRole('button', { name: 'Upgrade to Professional' }))
    fireEvent.click(within(await screen.findByTestId('offer-confirm')).getByRole('button', { name: 'Confirm' }))
    expect((await screen.findByRole('alert')).textContent).toBe(
      'The amount changed since you last looked. Check the new amount and confirm again.',
    )
    await waitFor(() => {
      expect(screen.getByTestId('confirm-text').textContent).toContain('Pay $15.00 now')
    })
    expect(screen.getByTestId('quote-pro').textContent).toContain('$15.00')
    fireEvent.click(within(screen.getByTestId('offer-confirm')).getByRole('button', { name: 'Confirm' }))
    await screen.findByText('Done. Your plan is now Professional.')
    const posts = api.to('POST', '/api/account/billing/checkout')
    expect(posts.map((c) => (c.body as { expected_amount_minor: number }).expected_amount_minor)).toEqual([1234, 1500])
    expect(posts[1]?.headers.get('Idempotency-Key')).not.toBe(posts[0]?.headers.get('Idempotency-Key'))
  })

  it('downgrades and cancels at the period end, with no charge and no refund', async () => {
    const api = fakeApi({
      ...ACCOUNT,
      'GET /api/account/plan': { ...INDIVIDUAL, plan: 'pro', name: 'Professional' },
      'GET /api/account/billing': billing(subscription({ plan: 'pro' }), [
        offer('instructor', 'Individual', 900, 'downgrade', 0),
        offer('pro', 'Professional', 2900, 'resume', 0),
      ]),
      'POST /api/account/billing/change': billing(subscription({ plan: 'pro', cancel_at_end: true }), [
        offer('pro', 'Professional', 2900, 'resume', 0),
      ]),
    })
    const options = await openPlan()
    fireEvent.click(await within(options).findByRole('button', { name: 'Switch to Individual at period end' }))
    expect((await screen.findByTestId('confirm-text')).textContent).toBe(
      'Switch to Individual on 15 December. Nothing is charged, and you keep your current plan until then. Money already paid is not refunded.',
    )
    fireEvent.click(screen.getByRole('button', { name: 'Go back' }))
    expect(screen.queryByTestId('offer-confirm')).toBeNull()
    fireEvent.click(within(options).getByRole('button', { name: 'Cancel subscription' }))
    expect((await screen.findByTestId('confirm-text')).textContent).toContain('Your subscription ends on 15 December')
    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }))
    await screen.findByText('Done. The change takes effect when your period ends.')
    expect(api.to('POST', '/api/account/billing/change')[0]?.body).toEqual({ plan: 'free' })
    expect(api.to('POST', '/api/account/billing/checkout')).toEqual([])
    // The cancel is scheduled: the subscription says so, and the plan can be kept.
    expect((await screen.findByTestId('plan-subscription')).textContent).toBe(
      'Subscription: Professional, ending on 15 December. After that you are on Free.',
    )
    expect(within(options).getByRole('button', { name: 'Keep Professional' })).toBeTruthy()
    expect(within(options).queryByRole('button', { name: 'Cancel subscription' })).toBeNull()
  })

  it('keeps the plan to remove a scheduled change', async () => {
    const api = fakeApi({
      ...ACCOUNT,
      'GET /api/account/plan': INDIVIDUAL,
      'GET /api/account/billing': billing(subscription({ cancel_at_end: true }), [offer('instructor', 'Individual', 900, 'resume', 0)]),
      'POST /api/account/billing/change': billing(subscription(), [offer('instructor', 'Individual', 900, 'resume', 0)]),
    })
    const options = await openPlan()
    fireEvent.click(await within(options).findByRole('button', { name: 'Keep Individual' }))
    fireEvent.click(within(await screen.findByTestId('offer-confirm')).getByRole('button', { name: 'Confirm' }))
    await screen.findByText('Done. Your plan stays as it is.')
    expect(api.to('POST', '/api/account/billing/change')[0]?.body).toEqual({ plan: 'instructor' })
  })

  it('says a plan has no price yet and offers nothing for it', async () => {
    fakeApi({
      ...ACCOUNT,
      'GET /api/account/billing': billing(null, [
        { plan: 'instructor', name: 'Individual', price_minor: null, currency: null, action: null, charge_minor: null, reason: 'price_missing' },
      ]),
    })
    const options = await openPlan()
    await within(options).findByText('No price has been set for this plan yet.')
    expect(within(options).queryByRole('button', { name: /Buy/ })).toBeNull()
  })

  it('shows a refusal in words, never the code', async () => {
    fakeApi({ ...ACCOUNT, 'GET /api/account/billing': NONE, 'POST /api/account/billing/checkout': () => json({ detail: { reason: 'outside_period' } }, 409) })
    const options = await openPlan()
    fireEvent.click(await within(options).findByRole('button', { name: 'Buy Individual' }))
    fireEvent.click(within(await screen.findByTestId('offer-confirm')).getByRole('button', { name: 'Confirm' }))
    expect((await screen.findByRole('alert')).textContent).toBe('That change cannot be made right now. Your plan is unchanged.')
  })
})
