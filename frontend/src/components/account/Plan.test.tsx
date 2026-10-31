import { fireEvent, screen, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { ApiError } from '../../api/client'
import { busyText, planRefusalText, quotaText } from '../../api/planMessages'
import { PLAN_FREE, SIGNED_IN, fakeApi, json, reason } from '../../test/fakeApi'
import { renderAt } from '../../test/render'

const ACCOUNT = {
  'GET /api/auth/session': SIGNED_IN,
  'GET /api/auth/identities': { identities: [], has_password: true, available: [] },
  'GET /api/auth/mfa': { totp_enabled: false, recovery_codes_left: 0 },
}

function quota(extra: Record<string, unknown>): ApiError {
  const body = { detail: { reason: 'quota', period_end: '2026-11-01T00:00:00Z', plan: 'free', ...extra } }
  return new ApiError(409, 'quota', null, body)
}

describe('the plan refusal sentences', () => {
  it('words a classes quota with the number and the period end', () => {
    expect(quotaText(quota({ quota: 'classes', limit: 3, used: 3 }))).toBe(
      'You have used your 3 classes for October. More from 1 November.',
    )
  })

  it('words a cost quota without any dollar figure', () => {
    const text = quotaText(quota({ quota: 'cost', limit: 5, used: 5 }))
    expect(text).toContain('for October')
    expect(text).toContain('More from 1 November.')
    expect(text).not.toContain('$')
  })

  it('words the backend cost body, which has no limit or used and a date-only end', () => {
    const body = { detail: { reason: 'quota', quota: 'cost', period_end: '2026-11-01', plan: 'free' } }
    const error = new ApiError(409, 'quota', null, body)
    expect(planRefusalText(error)).toBe(quotaText(error))
    expect(quotaText(error)).toContain('for October')
    expect(quotaText(error)).toContain('More from 1 November.')
  })

  it('uses the singular and degrades without numbers', () => {
    expect(quotaText(quota({ quota: 'classes', limit: 1, used: 1 }))).toContain('your 1 class for')
    expect(quotaText(new ApiError(409, 'quota', null, undefined))).toBe(
      'You have used your classes for this month. More at the start of next month.',
    )
  })

  it('words busy with the plan limit', () => {
    const error = new ApiError(429, 'busy', null, { detail: { reason: 'busy', limit: 2 } })
    expect(busyText(error)).toContain('allows 2 designs at a time')
    expect(busyText(new ApiError(429, 'busy', null, undefined))).toContain('already have designs running')
  })

  it('has a sentence for each redeem and trial refusal', () => {
    for (const code of ['code_invalid', 'already_redeemed', 'trial_used', 'trial_off']) {
      const text = planRefusalText(new ApiError(409, code, null, undefined))
      expect(text).not.toBeNull()
      expect(text).not.toContain(code)
    }
    expect(planRefusalText(new ApiError(409, 'other', null, undefined))).toBeNull()
  })
})

describe('the plan panel on the account page', () => {
  it('shows the plan, source, end, classes line and cost bar', async () => {
    fakeApi({
      ...ACCOUNT,
      'GET /api/account/plan': {
        ...PLAN_FREE,
        plan: 'instructor',
        name: 'Instructor',
        source: 'code',
        ends_at: '2026-12-15T00:00:00Z',
        limits: { classes_per_month: 30, revisions_per_class: 25, concurrent_jobs: 2 },
        classes_held: 3,
        cost_share: 0.4,
      },
    })
    renderAt('/account#plan')
    const name = await screen.findByTestId('plan-name')
    expect(name.textContent).toBe('Your plan: Instructor (from a code), until 15 December')
    expect(screen.getByTestId('plan-classes').textContent).toBe('3 of 30 classes in October')
    expect(screen.getByRole('progressbar', { name: /budget used/ }).getAttribute('aria-valuenow')).toBe('40')
    expect(screen.queryByRole('button', { name: /trial/i })).toBeNull()
  })

  it('redeems a code with CSRF and refreshes the panel', async () => {
    let held = 1
    const api = fakeApi({
      ...ACCOUNT,
      'GET /api/account/plan': () => ({ ...PLAN_FREE, classes_held: held }),
      'POST /api/account/redeem': () => {
        held = 2
        return { plan: 'instructor', features: [], ends_at: '2026-12-01T00:00:00Z' }
      },
    })
    renderAt('/account#plan')
    await screen.findByText('1 of 3 classes in October')
    fireEvent.change(screen.getByLabelText('Redeem a code'), { target: { value: ' ABC123 ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Redeem' }))
    await screen.findByText('2 of 3 classes in October')
    const [call] = api.to('POST', '/api/account/redeem')
    expect(call?.body).toEqual({ code: 'ABC123' })
    expect(call?.headers.get('X-CSRF-Token')).toBeTruthy()
    expect(screen.getByText('The code is redeemed.')).toBeTruthy()
  })

  it('says why a code was refused', async () => {
    fakeApi({ ...ACCOUNT, 'POST /api/account/redeem': () => reason(400, 'code_invalid') })
    renderAt('/account#plan')
    await screen.findByTestId('plan-name')
    fireEvent.change(screen.getByLabelText('Redeem a code'), { target: { value: 'nope' } })
    fireEvent.click(screen.getByRole('button', { name: 'Redeem' }))
    expect((await screen.findByRole('alert')).textContent).toContain('That code is not valid')
  })

  it('offers the trial only when available, starts it and refreshes', async () => {
    let started = false
    const api = fakeApi({
      ...ACCOUNT,
      'GET /api/account/plan': () =>
        started
          ? { ...PLAN_FREE, plan: 'instructor', name: 'Instructor', source: 'trial', trial: null }
          : { ...PLAN_FREE, trial: { plan: 'instructor', days: 14 } },
      'POST /api/account/trial': () => {
        started = true
        return { plan: 'instructor', features: [], ends_at: '2026-11-14T00:00:00Z' }
      },
    })
    renderAt('/account#plan')
    fireEvent.click(await screen.findByRole('button', { name: 'Start your 14-day trial' }))
    await waitFor(() => {
      expect(screen.getByTestId('plan-name').textContent).toContain('your trial')
    })
    expect(api.to('POST', '/api/account/trial')).toHaveLength(1)
    expect(screen.queryByRole('button', { name: /trial/i })).toBeNull()
  })

  it('says when a trial is no longer possible', async () => {
    fakeApi({
      ...ACCOUNT,
      'GET /api/account/plan': { ...PLAN_FREE, trial: { plan: 'instructor', days: 14 } },
      'POST /api/account/trial': () => reason(409, 'trial_used'),
    })
    renderAt('/account#plan')
    fireEvent.click(await screen.findByRole('button', { name: /trial/ }))
    expect((await screen.findByRole('alert')).textContent).toBe('You have already had your trial.')
  })
})

describe('the banner', () => {
  it('renders the text on every page, and never as HTML', async () => {
    fakeApi({
      'GET /api/settings/public': {
        banner: { text: 'Maintenance <b>tonight</b><img src=x onerror=alert(1)>', level: 'warning' },
        signup_mode: 'invite',
      },
    })
    renderAt('/signin')
    const banner = await screen.findByTestId('banner')
    expect(banner.textContent).toBe('Maintenance <b>tonight</b><img src=x onerror=alert(1)>')
    expect(banner.querySelector('b, img')).toBeNull()
    expect(banner.getAttribute('data-level')).toBe('warning')
  })

  it('is absent when there is no banner or the request fails', async () => {
    fakeApi({ 'GET /api/settings/public': () => json({ detail: { reason: 'x' } }, 500) })
    renderAt('/signin')
    await screen.findByRole('heading', { name: /sign in/i })
    expect(screen.queryByTestId('banner')).toBeNull()
  })
})
