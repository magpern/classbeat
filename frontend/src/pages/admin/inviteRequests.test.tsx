import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { SIGNED_IN, fakeApi, json, reason } from '../../test/fakeApi'
import { renderAt } from '../../test/render'

// ADR 0033, PB-8 (#986): the admin Invite requests screen on the real response shapes of
// backend/src/classbeat/admin/invite_requests.py; #989: one page at a time with the server's
// pending count, confirmation before a decline, the bulk decline, the mail toggle only when mail
// is configured, a reload after a stale approve and the plan names in the Issued tab; #994: a
// stale approve closes its form, and a page change drops an open decline confirmation.

const ADMIN = { ...SIGNED_IN, user: { ...SIGNED_IN.user, role: 'admin' } }
const BASE = '/api/admin/invite-requests'
const PENDING = `${BASE}?state=pending&offset=0&limit=50`
const ISSUED = `${BASE}?state=issued&offset=0&limit=50`
const DECLINED = `${BASE}?state=declined&offset=0&limit=50`
const CODE = 'INV1TE7C0DE5H0WN0NCE'
const STEPUP_CODE = '424242'
const A = 'aaaaaaaa-0000-0000-0000-000000000001'
const B = 'aaaaaaaa-0000-0000-0000-000000000002'

function request(id: string, email: string, more: Record<string, unknown> = {}) {
  return {
    id,
    email,
    heard_from: 'a friend <b>at</b> the studio',
    instructor: true,
    locale: 'en',
    has_account: false,
    state: 'pending',
    plan_id: null,
    grant_days: null,
    invite: null,
    decided_by: null,
    decided_at: null,
    created_at: '2026-11-10T10:00:00Z',
    ...more,
  }
}

function plan(id: string, name: string, rank: number) {
  return {
    id,
    name,
    rank,
    classes_per_month: 3,
    revisions_per_class: 3,
    concurrent_jobs: 1,
    monthly_cost_micros: 1,
    max_class_minutes: 60,
    games_allowed: true,
    races_allowed: true,
    ftp_tests_allowed: true,
    monthly_price_cents: null,
    intro_price_cents: null,
    intro_price_until: null,
    currency: null,
    saves_per_hour: null,
    saves_per_day: null,
    features: [],
    updated_at: '2026-10-31T08:00:00Z',
    updated_by: null,
  }
}

const SETTINGS = {
  flags: [],
  plans: [plan('pro', 'Pro', 2), plan('free', 'Free', 0), plan('instructor', 'Instructor', 1)],
  bounds: {
    classes_per_month: 200,
    revisions_per_class: 25,
    concurrent_jobs: 2,
    monthly_cost_micros: 3,
    max_class_minutes: 180,
    trial_days: 30,
    saves_per_hour: 30,
    saves_per_day: 100,
  },
  read_only: {
    agent_job_cost_micros: 1,
    song_job_cost_micros: 1,
    billing_enabled: false,
    mail_configured: true,
    unused_tier_invites: 0,
  },
}

function adminApi(routes: Record<string, unknown> = {}) {
  return fakeApi({
    'GET /api/auth/session': ADMIN,
    'GET /api/admin/whoami': { email: 'ann@example.test' },
    'GET /api/admin/settings': SETTINGS,
    [`GET ${PENDING}`]: {
      requests: [request(A, 'ann.rider@example.test'), request(B, 'bo@example.test', { has_account: true })],
      next_offset: null,
      pending: 2,
    },
    [`GET ${ISSUED}`]: { requests: [], next_offset: null, pending: 2 },
    [`GET ${DECLINED}`]: { requests: [], next_offset: null, pending: 2 },
    ...(routes as Record<string, object>),
  })
}

afterEach(() => {
  window.localStorage.clear()
  window.sessionStorage.clear()
})

async function approveFirst() {
  const row = (await screen.findByText('ann.rider@example.test')).closest('tr')!
  fireEvent.click(within(row).getByRole('button', { name: 'Approve' }))
  return screen.findByRole('form', { name: 'Approve this request' })
}

describe('admin invite requests', () => {
  it('is in the admin navigation and lists the pending queue against the cap, heard-from as text', async () => {
    adminApi()
    renderAt('/admin/invite-requests')
    expect(await screen.findByText('ann.rider@example.test')).toBeInTheDocument()
    expect(screen.getByTestId('queue-state')).toHaveTextContent('Pending: 2 of 1000.')
    expect(
      within(screen.getByRole('navigation', { name: 'Admin' })).getByRole('link', {
        name: 'Invite requests',
      }),
    ).toHaveAttribute('aria-current', 'page')
    // Plain text, never markup.
    expect(screen.getAllByText('a friend <b>at</b> the studio')).toHaveLength(2)
    expect(document.querySelector('td b')).toBeNull()
    expect(screen.getByText('Has an account')).toBeInTheDocument()
  })

  it('reads one page and takes the pending count from the server', async () => {
    const first = Array.from({ length: 50 }, (_, i) =>
      request(`aaaaaaaa-0000-0000-0000-${String(i).padStart(12, '0')}`, `p${String(i)}@example.test`),
    )
    const api = adminApi({
      [`GET ${PENDING}`]: { requests: first, next_offset: 50, pending: 731 },
      [`GET ${BASE}?state=pending&offset=50&limit=50`]: {
        requests: [request(B, 'last@example.test')],
        next_offset: null,
        pending: 731,
      },
    })
    renderAt('/admin/invite-requests')
    expect(await screen.findByText('p0@example.test')).toBeInTheDocument()
    expect(screen.getByTestId('queue-state')).toHaveTextContent('Pending: 731 of 1000.')
    expect(screen.queryByText('last@example.test')).toBeNull()
    expect(api.calls.filter((c) => c.path.startsWith(`${BASE}?state=pending`))).toHaveLength(1)
    fireEvent.click(screen.getByRole('button', { name: 'Older' }))
    expect(await screen.findByText('last@example.test')).toBeInTheDocument()
    expect(screen.queryByText('p0@example.test')).toBeNull()
    expect(api.calls.filter((c) => c.path.startsWith(`${BASE}?state=pending`))).toHaveLength(2)
  })

  it('says so when the queue is full', async () => {
    adminApi({
      [`GET ${PENDING}`]: { requests: [request(A, 'ann.rider@example.test')], next_offset: 50, pending: 1000 },
    })
    renderAt('/admin/invite-requests')
    expect(await screen.findByText(/The queue is full/)).toBeInTheDocument()
    expect(screen.getByTestId('queue-state')).toHaveTextContent('Pending: 1000 of 1000.')
  })

  it('approves with a tier after a step-up and shows the code once, with copy', async () => {
    const writeText = vi.fn(() => Promise.resolve())
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText },
      configurable: true,
    })
    let stepped = false
    const issued = request(A, 'ann.rider@example.test', {
      state: 'issued',
      plan_id: 'instructor',
      grant_days: 30,
      invite: { id: 'i1', state: 'active' },
      decided_by: { id: 'u1', email: 'ann@example.test' },
      decided_at: '2026-11-12T10:00:00Z',
    })
    const api = adminApi({
      [`POST ${BASE}/${A}/approve`]: () =>
        stepped
          ? json({ code: CODE, request: issued, email: 'not_requested' }, 201)
          : reason(403, 'stepup_required'),
      'POST /api/auth/mfa/stepup': () => {
        stepped = true
        return { ok: true }
      },
    })
    renderAt('/admin/invite-requests')
    const form = await approveFirst()
    // The plans are the settings list, lowest rank first, then no tier.
    expect(
      within(form)
        .getAllByRole('option')
        .map((o) => o.textContent),
    ).toEqual(['No tier (plain invite)', 'Free', 'Instructor', 'Pro'])
    await waitFor(() => {
      expect(within(form).getByRole('option', { name: 'Instructor' })).toBeInTheDocument()
    })
    fireEvent.change(within(form).getByLabelText('Tier'), {
      target: { value: 'instructor' },
    })
    fireEvent.change(within(form).getByLabelText('Days the tier lasts'), {
      target: { value: '45' },
    })
    fireEvent.click(within(form).getByRole('button', { name: 'Approve and create code' }))

    const dialog = await screen.findByRole('dialog', {
      name: 'Enter a two-factor code',
    })
    fireEvent.change(within(dialog).getByLabelText('Authenticator or recovery code'), {
      target: { value: STEPUP_CODE },
    })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Continue' }))

    const panel = await screen.findByTestId('one-time-code')
    expect(within(panel).getByText(CODE)).toBeInTheDocument()
    expect(panel).toHaveTextContent('Send this code to ann.rider@example.test yourself.')
    fireEvent.click(within(panel).getByRole('button', { name: 'Copy' }))
    expect(await within(panel).findByText('Copied.')).toBeInTheDocument()
    expect(writeText).toHaveBeenCalledWith(CODE)
    const sent = api.to('POST', `${BASE}/${A}/approve`)
    expect(sent).toHaveLength(2)
    expect(sent[1]?.body).toEqual({
      expires_days: 14,
      send_email: false,
      plan_id: 'instructor',
      grant_days: 45,
    })

    // Closing, or leaving, drops the code.
    fireEvent.click(within(panel).getByRole('button', { name: 'Done' }))
    expect(document.body.innerHTML).not.toContain(CODE)
    expect(window.location.href).not.toContain(CODE)
    expect(window.localStorage.length + window.sessionStorage.length).toBe(0)
  })

  it('approves a plain invite without plan fields and says when mail was off', async () => {
    const api = adminApi({
      [`POST ${BASE}/${A}/approve`]: () =>
        json(
          {
            code: CODE,
            request: request(A, 'ann.rider@example.test', { state: 'issued' }),
            email: 'mail_off',
          },
          201,
        ),
    })
    renderAt('/admin/invite-requests')
    const form = await approveFirst()
    fireEvent.click(within(form).getByLabelText('Email the code to the requester'))
    fireEvent.change(within(form).getByLabelText('Invite valid for days'), {
      target: { value: '7' },
    })
    fireEvent.click(within(form).getByRole('button', { name: 'Approve and create code' }))
    const panel = await screen.findByTestId('one-time-code')
    expect(panel).toHaveTextContent('Mail is not set up on the server, so nothing was emailed.')
    expect(api.to('POST', `${BASE}/${A}/approve`)[0]?.body).toEqual({
      expires_days: 7,
      send_email: true,
    })
  })

  it('says the code is being emailed when mail went out', async () => {
    adminApi({
      [`POST ${BASE}/${A}/approve`]: () =>
        json(
          {
            code: CODE,
            request: request(A, 'ann.rider@example.test', { state: 'issued' }),
            email: 'queued',
          },
          201,
        ),
    })
    renderAt('/admin/invite-requests')
    const form = await approveFirst()
    fireEvent.click(within(form).getByRole('button', { name: 'Approve and create code' }))
    expect(await screen.findByTestId('one-time-code')).toHaveTextContent(
      'is also being emailed to ann.rider@example.test',
    )
  })

  it.each([
    ['unknown_plan', 422, 'There is no such plan.'],
    ['already_decided', 409, 'That request was decided already. Reload the list.'],
  ])('shows %s in words and keeps no code', async (why, status, text) => {
    adminApi({ [`POST ${BASE}/${A}/approve`]: () => reason(status, why) })
    renderAt('/admin/invite-requests')
    const form = await approveFirst()
    fireEvent.click(within(form).getByRole('button', { name: 'Approve and create code' }))
    expect(await screen.findByText(text)).toBeInTheDocument()
    expect(screen.queryByTestId('one-time-code')).toBeNull()
  })

  it('will not send an expiry outside 1 to 30 days', async () => {
    const api = adminApi()
    renderAt('/admin/invite-requests')
    const form = await approveFirst()
    fireEvent.change(within(form).getByLabelText('Invite valid for days'), {
      target: { value: '31' },
    })
    expect(within(form).getByRole('button', { name: 'Approve and create code' })).toBeDisabled()
    expect(api.to('POST', `${BASE}/${A}/approve`)).toHaveLength(0)
  })

  it('asks before a decline, then declines and reloads the queue', async () => {
    let declined = false
    const api = adminApi({
      [`GET ${PENDING}`]: () =>
        json({
          requests: declined
            ? [request(B, 'bo@example.test')]
            : [request(A, 'ann.rider@example.test'), request(B, 'bo@example.test')],
          next_offset: null,
          pending: declined ? 1 : 2,
        }),
      [`POST ${BASE}/${A}/decline`]: () => {
        declined = true
        return request(A, 'ann.rider@example.test', { state: 'declined' })
      },
    })
    renderAt('/admin/invite-requests')
    const row = (await screen.findByText('ann.rider@example.test')).closest('tr')!
    fireEvent.click(within(row).getByRole('button', { name: 'Decline' }))
    // Nothing is sent until the admin confirms; Keep sends nothing.
    let confirm = screen.getByRole('group', { name: 'Confirm the decline' })
    expect(confirm).toHaveTextContent('Decline the request from ann.rider@example.test? This cannot be undone.')
    fireEvent.click(within(confirm).getByRole('button', { name: 'Keep' }))
    expect(screen.queryByRole('group', { name: 'Confirm the decline' })).toBeNull()
    expect(api.to('POST', `${BASE}/${A}/decline`)).toHaveLength(0)
    fireEvent.click(within(row).getByRole('button', { name: 'Decline' }))
    confirm = screen.getByRole('group', { name: 'Confirm the decline' })
    fireEvent.click(within(confirm).getByRole('button', { name: 'Yes, decline' }))
    await waitFor(() => {
      expect(screen.queryByText('ann.rider@example.test')).toBeNull()
    })
    expect(screen.getByTestId('queue-state')).toHaveTextContent('Pending: 1 of 1000.')
    expect(screen.getByText('Declined 1 request.')).toBeInTheDocument()
    expect(api.to('POST', `${BASE}/${A}/decline`)).toHaveLength(1)
    expect(api.to('POST', `${BASE}/decline`)).toHaveLength(0)
  })

  it('declines the selected requests in one call after asking, and says which were decided already', async () => {
    const C = 'aaaaaaaa-0000-0000-0000-000000000003'
    let declined = false
    const api = adminApi({
      [`GET ${PENDING}`]: () =>
        json({
          requests: declined
            ? [request(C, 'cy@example.test')]
            : [request(A, 'ann.rider@example.test'), request(B, 'bo@example.test'), request(C, 'cy@example.test')],
          next_offset: null,
          pending: declined ? 1 : 3,
        }),
      [`POST ${BASE}/decline`]: () => {
        declined = true
        return { declined: [A], skipped: [B] }
      },
    })
    renderAt('/admin/invite-requests')
    await screen.findByText('ann.rider@example.test')
    expect(screen.queryByRole('button', { name: /Decline selected/ })).toBeNull()
    fireEvent.click(screen.getByRole('checkbox', { name: 'Select every request on this page' }))
    fireEvent.click(screen.getByRole('checkbox', { name: 'Select cy@example.test' }))
    fireEvent.click(screen.getByRole('button', { name: 'Decline selected (2)' }))
    const confirm = screen.getByRole('group', { name: 'Confirm the decline' })
    expect(confirm).toHaveTextContent('Decline 2 requests? This cannot be undone.')
    expect(api.to('POST', `${BASE}/decline`)).toHaveLength(0)
    fireEvent.click(within(confirm).getByRole('button', { name: 'Yes, decline' }))
    expect(await screen.findByText('Declined 1 request. 1 request had been decided already.')).toBeInTheDocument()
    await waitFor(() => {
      expect(screen.queryByText('ann.rider@example.test')).toBeNull()
    })
    expect(api.to('POST', `${BASE}/decline`)[0]?.body).toEqual({ ids: [A, B] })
    // The selection is cleared with the reload.
    expect(screen.getByRole('checkbox', { name: 'Select cy@example.test' })).not.toBeChecked()
    expect(screen.queryByRole('button', { name: /Decline selected/ })).toBeNull()
  })

  it('offers no email toggle when mail is not configured, and sends none', async () => {
    const api = adminApi({
      'GET /api/admin/settings': { ...SETTINGS, read_only: { ...SETTINGS.read_only, mail_configured: false } },
      [`POST ${BASE}/${A}/approve`]: () =>
        json({ code: CODE, request: request(A, 'ann.rider@example.test', { state: 'issued' }), email: 'not_requested' }, 201),
    })
    renderAt('/admin/invite-requests')
    const form = await approveFirst()
    expect(await within(form).findByTestId('mail-off')).toHaveTextContent('Mail is not set up on the server')
    expect(within(form).queryByLabelText('Email the code to the requester')).toBeNull()
    fireEvent.click(within(form).getByRole('button', { name: 'Approve and create code' }))
    await screen.findByTestId('one-time-code')
    expect(api.to('POST', `${BASE}/${A}/approve`)[0]?.body).toEqual({ expires_days: 14, send_email: false })
  })

  it('closes the form and reloads the queue when the request was decided meanwhile', async () => {
    const api = adminApi({ [`POST ${BASE}/${A}/approve`]: () => reason(409, 'already_decided') })
    renderAt('/admin/invite-requests')
    const form = await approveFirst()
    expect(api.to('GET', PENDING)).toHaveLength(1)
    fireEvent.click(within(form).getByRole('button', { name: 'Approve and create code' }))
    expect(await screen.findByText('That request was decided already. Reload the list.')).toBeInTheDocument()
    await waitFor(() => {
      expect(api.to('GET', PENDING)).toHaveLength(2)
    })
    // #994: nothing is left to approve, so the form is gone; the words stay until the next approve.
    expect(screen.queryByRole('form', { name: 'Approve this request' })).toBeNull()
    expect(screen.queryByTestId('one-time-code')).toBeNull()
    expect(api.to('POST', `${BASE}/${A}/approve`)).toHaveLength(1)
    await approveFirst()
    expect(screen.queryByText('That request was decided already. Reload the list.')).toBeNull()
  })

  it('drops an open decline confirmation when the page changes', async () => {
    const api = adminApi({
      [`GET ${PENDING}`]: { requests: [request(A, 'ann.rider@example.test')], next_offset: 50, pending: 2 },
      [`GET ${BASE}?state=pending&offset=50&limit=50`]: {
        requests: [request(B, 'bo@example.test')],
        next_offset: null,
        pending: 2,
      },
    })
    renderAt('/admin/invite-requests')
    const row = (await screen.findByText('ann.rider@example.test')).closest('tr')!
    fireEvent.click(within(row).getByRole('button', { name: 'Decline' }))
    expect(screen.getByRole('group', { name: 'Confirm the decline' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Older' }))
    expect(await screen.findByText('bo@example.test')).toBeInTheDocument()
    // #994: the confirmation named a request of the other page, so it cannot be confirmed here.
    expect(screen.queryByRole('group', { name: 'Confirm the decline' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Newest' }))
    expect(await screen.findByText('ann.rider@example.test')).toBeInTheDocument()
    expect(screen.queryByRole('group', { name: 'Confirm the decline' })).toBeNull()
    expect(api.calls.filter((c) => c.method === 'POST')).toHaveLength(0)
  })

  it('shows the issued and declined tabs with the decision and the plan by name', async () => {
    adminApi({
      [`GET ${ISSUED}`]: {
        requests: [
          request(A, 'issued@example.test', {
            state: 'issued',
            plan_id: 'pro',
            grant_days: 30,
            invite: { id: 'i1', state: 'used' },
            decided_by: { id: 'u1', email: 'ann@example.test' },
            decided_at: '2026-11-11T10:00:00Z',
          }),
          request(B, 'gone@example.test', {
            state: 'issued',
            plan_id: 'retired',
            grant_days: 7,
            decided_by: null,
            decided_at: '2026-11-11T10:00:00Z',
          }),
        ],
        next_offset: null,
        pending: 2,
      },
    })
    renderAt('/admin/invite-requests')
    await screen.findByText('ann.rider@example.test')
    fireEvent.click(screen.getByRole('radio', { name: 'Issued' }))
    const row = (await screen.findByText('issued@example.test')).closest('tr')!
    expect(row).toHaveTextContent('Pro for 30 days')
    expect(row).toHaveTextContent('used')
    expect(row).toHaveTextContent('ann@example.test')
    // A plan that is no longer listed keeps its id.
    expect(screen.getByText('gone@example.test').closest('tr')).toHaveTextContent('retired for 7 days')
    expect(within(row).queryByRole('button', { name: 'Approve' })).toBeNull()
    expect(within(row).queryByRole('checkbox')).toBeNull()
    expect(screen.queryByTestId('queue-state')).toBeNull()
    fireEvent.click(screen.getByRole('radio', { name: 'Declined' }))
    expect(await screen.findByText('No declined requests.')).toBeInTheDocument()
  })

  it('shows a refused list in words', async () => {
    adminApi({ [`GET ${PENDING}`]: () => reason(403, 'forbidden') })
    renderAt('/admin/invite-requests')
    expect(await screen.findByText('Your account is not an admin account.')).toBeInTheDocument()
  })
})
