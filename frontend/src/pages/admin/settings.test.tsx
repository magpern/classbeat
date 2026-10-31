import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { SIGNED_IN, fakeApi, reason } from '../../test/fakeApi'
import { renderAt } from '../../test/render'
import { REASONS } from './Settings'

// The admin Settings page and the refund button (#226 part 2): the real response shapes of
// backend/src/classbeat/admin/settings.py and usage.py, flags the environment forbids shown
// disabled with their reason, refused writes shown in words, clear and step-up.

const ADMIN = { ...SIGNED_IN, user: { ...SIGNED_IN.user, role: 'admin' } }
const ANN = { id: 'bbbbbbbb-0000-0000-0000-000000000002', email: 'ann@example.test' }
const SETTINGS = '/api/admin/settings'
const USER_ID = '11111111-2222-3333-4444-555555555555'
const LEDGER_ID = 'dddddddd-0000-0000-0000-000000000004'
const STEPUP_CODE = '424242'

function flagRow(key: string, values: Record<string, unknown>) {
  return {
    key,
    source: 'environment',
    effective: null,
    environment: null,
    stored: null,
    stored_valid: true,
    updated_at: null,
    updated_by: null,
    ...values,
  }
}

function plan(id: string, name: string, rank: number, limits: [number, number, number, number]) {
  const [classes, revisions, jobs, cost] = limits
  return {
    id,
    name,
    rank,
    classes_per_month: classes,
    revisions_per_class: revisions,
    concurrent_jobs: jobs,
    monthly_cost_micros: cost,
    features: [],
    updated_at: '2026-10-31T08:00:00Z',
    updated_by: null,
  }
}

function view(
  overrides: { songPicking?: string; swedebeat?: string; trialDays?: number; signupStored?: string | null } = {},
) {
  const stored = overrides.signupStored ?? null
  return {
    flags: [
      flagRow('signup_mode', {
        source: stored === 'closed' ? 'database' : 'environment',
        effective: stored ?? 'invite',
        environment: 'invite',
        stored,
        updated_at: stored === null ? null : '2026-10-31T09:00:00Z',
        updated_by: stored === null ? null : ANN,
      }),
      flagRow('song_picking', { effective: 'off', environment: overrides.songPicking ?? 'off' }),
      flagRow('swedebeat_enabled', { effective: 'off', environment: overrides.swedebeat ?? 'off' }),
      flagRow('trial', { environment: { plan: null, days: 0 } }),
      flagRow('banner', {}),
    ],
    plans: [
      plan('free', 'Free', 0, [3, 3, 1, 10_000_000]),
      plan('instructor', 'Instructor', 1, [30, 10, 2, 60_000_000]),
      plan('pro', 'Pro', 2, [100, 25, 2, 200_000_000]),
    ],
    bounds: {
      classes_per_month: 200,
      revisions_per_class: 25,
      concurrent_jobs: 2,
      monthly_cost_micros: 300_000_000,
      trial_days: overrides.trialDays ?? 30,
    },
    read_only: {
      agent_job_cost_micros: 1_500_000,
      song_job_cost_micros: 3_000_000,
      billing_enabled: false,
    },
  }
}

function adminApi(routes: Record<string, unknown>) {
  return fakeApi({
    'GET /api/auth/session': ADMIN,
    'GET /api/admin/whoami': { email: ANN.email },
    ...(routes as Record<string, object>),
  })
}

function card(title: string): HTMLElement {
  return screen.getByRole('region', { name: title })
}

async function enterStepUpCode() {
  const dialog = await screen.findByRole('dialog', { name: 'Enter a two-factor code' })
  fireEvent.change(within(dialog).getByLabelText('Authenticator or recovery code'), { target: { value: STEPUP_CODE } })
  fireEvent.click(within(dialog).getByRole('button', { name: 'Continue' }))
}

describe('admin settings', () => {
  it('shows what the environment forbids as disabled, with the reason', async () => {
    adminApi({ [`GET ${SETTINGS}`]: view({ songPicking: 'off', swedebeat: 'off', trialDays: 0 }) })
    renderAt('/admin/settings')
    const song = await screen.findByLabelText('Song picking for')
    expect(song).toBeDisabled()
    expect(song).toHaveAccessibleDescription(REASONS.songPickingOff)
    expect(within(card('Song picking')).getByRole('button', { name: 'Save' })).toBeDisabled()
    expect(screen.getByLabelText('Trial plan')).toBeDisabled()
    expect(screen.getByLabelText('Trial plan')).toHaveAccessibleDescription(REASONS.trialsOff)
    expect(screen.getByLabelText('Days (0 is off)')).toBeDisabled()
    const signup = screen.getByLabelText('Sign-up mode')
    expect(within(signup).getByRole('option', { name: 'Open (server only)' })).toBeDisabled()
    expect(signup).toHaveAccessibleDescription(REASONS.openSignup)
    const swedebeat = screen.getByLabelText('Swedebeat sign-in for')
    expect(swedebeat).toBeDisabled()
    expect(swedebeat).toHaveAccessibleDescription(REASONS.swedebeatOff)
    expect(within(card('Swedebeat')).getByRole('button', { name: 'Save' })).toBeDisabled()
    const billing = screen.getByRole('switch', { name: 'Billing' })
    expect(billing).toBeDisabled()
    expect(billing).toHaveAccessibleDescription(REASONS.billing)
    expect(screen.queryByRole('switch', { name: 'Swedebeat' })).toBeNull() // a real flag now, not read-only
    expect(screen.getByText('$1.50')).toBeInTheDocument()
  })

  it('offers song picking and trials when the environment allows them, plans above Instructor disabled', async () => {
    adminApi({ [`GET ${SETTINGS}`]: view({ songPicking: 'on' }) })
    renderAt('/admin/settings')
    expect(await screen.findByLabelText('Song picking for')).toBeEnabled()
    const trialPlan = screen.getByLabelText('Trial plan')
    expect(trialPlan).toBeEnabled()
    expect(within(trialPlan).getByRole('option', { name: 'Pro (not for trials)' })).toBeDisabled()
    expect(within(trialPlan).getByRole('option', { name: 'Instructor' })).toBeEnabled()
  })

  it('lets the admin switch Swedebeat off or on when the server allows it', async () => {
    const api = adminApi({
      [`GET ${SETTINGS}`]: view({ swedebeat: 'on' }),
      'PUT /api/admin/settings/swedebeat_enabled': flagRow('swedebeat_enabled', {
        source: 'database',
        effective: 'off',
        environment: 'on',
        stored: 'off',
      }),
    })
    renderAt('/admin/settings')
    const select = await screen.findByLabelText('Swedebeat sign-in for')
    expect(select).toBeEnabled()
    fireEvent.change(select, { target: { value: 'off' } })
    fireEvent.click(within(card('Swedebeat')).getByRole('button', { name: 'Save' }))
    await waitFor(() => {
      expect(api.to('PUT', '/api/admin/settings/swedebeat_enabled')).toHaveLength(1)
    })
    expect(api.to('PUT', '/api/admin/settings/swedebeat_enabled')[0]?.body).toEqual({ value: 'off' })
  })

  it('saves a flag through the step-up and shows where the value comes from', async () => {
    let saved = false
    let stepped = false
    const api = adminApi({
      [`GET ${SETTINGS}`]: () => view({ signupStored: saved ? 'closed' : null }),
      'PUT /api/admin/settings/signup_mode': () => {
        if (!stepped) return reason(403, 'stepup_required')
        saved = true
        return view({ signupStored: 'closed' }).flags[0]
      },
      'POST /api/auth/mfa/stepup': () => {
        stepped = true
        return { ok: true }
      },
    })
    renderAt('/admin/settings')
    const signup = await screen.findByLabelText('Sign-up mode')
    expect(within(screen.getByTestId('flag-signup_mode')).getByText('from the environment')).toBeInTheDocument()
    fireEvent.change(signup, { target: { value: 'closed' } })
    fireEvent.click(within(card('Sign-up')).getByRole('button', { name: 'Save' }))
    await enterStepUpCode()
    await waitFor(() => {
      expect(within(screen.getByTestId('flag-signup_mode')).getByText('set here')).toBeInTheDocument()
    })
    expect(api.to('PUT', '/api/admin/settings/signup_mode').map((c) => c.body)).toEqual([
      { value: 'closed' },
      { value: 'closed' },
    ])
    expect(within(card('Sign-up')).getByText(/by ann@example.test/)).toBeInTheDocument()
  })

  it('shows a refused write’s 422 reason in words and keeps the form', async () => {
    adminApi({
      [`GET ${SETTINGS}`]: view({ songPicking: 'on' }),
      'PUT /api/admin/settings/trial': reason(422, 'above_environment_bound'),
    })
    renderAt('/admin/settings')
    const days = await screen.findByLabelText('Days (0 is off)')
    fireEvent.change(days, { target: { value: '30' } })
    fireEvent.click(within(card('Trial')).getByRole('button', { name: 'Save' }))
    expect(await within(card('Trial')).findByText('That is more than the server allows.')).toBeInTheDocument()
    expect(screen.getByLabelText('Days (0 is off)')).toHaveValue(30)
  })

  it('clears a stored flag with DELETE, so the environment applies again', async () => {
    let cleared = false
    const api = adminApi({
      [`GET ${SETTINGS}`]: () => view({ signupStored: cleared ? null : 'closed' }),
      'DELETE /api/admin/settings/signup_mode': () => {
        cleared = true
        return view().flags[0]
      },
    })
    renderAt('/admin/settings')
    fireEvent.click(await screen.findByRole('button', { name: "Use the environment's value" }))
    await waitFor(() => {
      expect(within(screen.getByTestId('flag-signup_mode')).getByText('from the environment')).toBeInTheDocument()
    })
    expect(api.to('DELETE', '/api/admin/settings/signup_mode')).toHaveLength(1)
    expect(screen.queryByRole('button', { name: "Use the environment's value" })).toBeNull()
  })

  it('sends only the plan limits that changed, and shows a refusal in words', async () => {
    let answer: unknown = reason(422, 'above_environment_bound')
    const api = adminApi({
      [`GET ${SETTINGS}`]: view(),
      'PATCH /api/admin/plans/instructor': () => answer,
    })
    renderAt('/admin/settings')
    const form = await screen.findByRole('form', { name: 'Instructor limits' })
    fireEvent.change(within(form).getByLabelText('Classes at a time'), { target: { value: '9' } })
    fireEvent.change(within(form).getByLabelText('Monthly cost cap (US dollars, estimate)'), { target: { value: '75.50' } })
    fireEvent.click(within(form).getByRole('button', { name: 'Save Instructor' }))
    expect(await within(form).findByText('That is more than the server allows.')).toBeInTheDocument()
    expect(api.to('PATCH', '/api/admin/plans/instructor').map((c) => c.body)).toEqual([
      { concurrent_jobs: 9, monthly_cost_micros: 75_500_000 },
    ])
    answer = plan('instructor', 'Instructor', 1, [30, 10, 2, 75_500_000])
    fireEvent.change(within(form).getByLabelText('Classes at a time'), { target: { value: '2' } })
    fireEvent.click(within(form).getByRole('button', { name: 'Save Instructor' }))
    await waitFor(() => {
      expect(api.to('PATCH', '/api/admin/plans/instructor')).toHaveLength(2)
    })
    expect(api.to('PATCH', '/api/admin/plans/instructor')[1]?.body).toEqual({ monthly_cost_micros: 75_500_000 })
  })

  it('refuses a malformed number before sending anything', async () => {
    const api = adminApi({ [`GET ${SETTINGS}`]: view() })
    renderAt('/admin/settings')
    const form = await screen.findByRole('form', { name: 'Free limits' })
    fireEvent.change(within(form).getByLabelText('Classes per month'), { target: { value: '2.5' } })
    fireEvent.click(within(form).getByRole('button', { name: 'Save Free' }))
    expect(await within(form).findByText('Use whole numbers, and dollars with at most two decimals.')).toBeInTheDocument()
    expect(api.to('PATCH', '/api/admin/plans/free')).toHaveLength(0)
  })
})

const DETAIL = {
  id: USER_ID,
  email: 'rider@example.test',
  created_at: '2026-10-01T10:00:00Z',
  role: 'user',
  disabled_at: null,
  plan: { id: 'free', name: 'Free', source: null, ends_at: null },
  features: [],
  classes_held: 1,
  classes_limit: 3,
  cost_micros: 1_250_000,
  entitlements: [],
  connections: [],
}

function ledgerRow(id: string, state: string, why: string | null = null) {
  return {
    id,
    job_id: null,
    period: '2026-10-01',
    plan_id: 'free',
    state,
    reason: why,
    settled_at: state === 'reserved' ? null : '2026-10-30T10:00:00Z',
    refunded_by: null,
    created_at: '2026-10-30T09:00:00Z',
  }
}

const REFUND = `/api/admin/users/${USER_ID}/credits/${LEDGER_ID}/refund`

describe('admin refund', () => {
  it('refunds a used credit after a confirm and a step-up; only used credits offer it', async () => {
    let refunded = false
    let stepped = false
    const api = adminApi({
      [`GET /api/admin/users/${USER_ID}`]: () => ({
        ...DETAIL,
        ledger: [
          ledgerRow(LEDGER_ID, refunded ? 'refunded' : 'committed', refunded ? 'admin_refund' : null),
          ledgerRow('eeeeeeee-0000-0000-0000-000000000005', 'reserved'),
          ledgerRow('ffffffff-0000-0000-0000-000000000006', 'released', 'cancelled'),
        ],
      }),
      [`POST ${REFUND}`]: () => {
        if (!stepped) return reason(403, 'stepup_required')
        refunded = true
        return { id: LEDGER_ID, state: 'refunded', classes_held: 0 }
      },
      'POST /api/auth/mfa/stepup': () => {
        stepped = true
        return { ok: true }
      },
    })
    renderAt(`/admin/users/${USER_ID}`)
    const credits = await screen.findByRole('table', { name: 'Credits' })
    expect(within(credits).getAllByRole('button', { name: 'Refund' })).toHaveLength(1)
    fireEvent.click(within(credits).getByRole('button', { name: 'Refund' }))
    expect(api.to('POST', REFUND)).toHaveLength(0)
    fireEvent.click(within(credits).getByRole('button', { name: 'Confirm refund' }))
    await enterStepUpCode()
    expect(await screen.findByText('Refunded. The class no longer counts this month.')).toBeInTheDocument()
    expect(api.to('POST', REFUND)).toHaveLength(2)
    await waitFor(() => {
      expect(within(screen.getByRole('table', { name: 'Credits' })).queryByRole('button', { name: 'Refund' })).toBeNull()
    })
    expect(within(screen.getByRole('table', { name: 'Credits' })).getByText(/refunded \(admin refund\)/)).toBeInTheDocument()
  })

  it.each([
    ['already_refunded', 'That credit was refunded already.'],
    ['not_committed', /Only a used credit can be refunded/],
  ])('shows a 409 %s in words', async (why, words) => {
    adminApi({
      [`GET /api/admin/users/${USER_ID}`]: { ...DETAIL, ledger: [ledgerRow(LEDGER_ID, 'committed')] },
      [`POST ${REFUND}`]: reason(409, why),
    })
    renderAt(`/admin/users/${USER_ID}`)
    fireEvent.click(await screen.findByRole('button', { name: 'Refund' }))
    fireEvent.click(screen.getByRole('button', { name: 'Confirm refund' }))
    expect(await screen.findByText(words)).toBeInTheDocument()
  })

  it('can be cancelled without a request', async () => {
    const api = adminApi({
      [`GET /api/admin/users/${USER_ID}`]: { ...DETAIL, ledger: [ledgerRow(LEDGER_ID, 'committed')] },
    })
    renderAt(`/admin/users/${USER_ID}`)
    fireEvent.click(await screen.findByRole('button', { name: 'Refund' }))
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.getByRole('button', { name: 'Refund' })).toBeInTheDocument()
    expect(api.to('POST', REFUND)).toHaveLength(0)
  })
})
