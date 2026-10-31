import { act, fireEvent, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { auditPath, EMPTY_FILTER, type AuditFilter } from '../../api/adminViews'
import { SIGNED_IN, fakeApi, reason } from '../../test/fakeApi'
import { renderAt } from '../../test/render'

// The admin Usage, Jobs and Audit log pages (#226): what they show, amounts labelled as
// estimates, and the audit log's filters round-tripping through the page's address.

const ADMIN = { ...SIGNED_IN, user: { ...SIGNED_IN.user, role: 'admin' } }
const USER_ID = '11111111-2222-3333-4444-555555555555'
const SUMS = {
  runs: 3,
  jobs: 2,
  input_tokens: 1200,
  output_tokens: 3400,
  cache_read_tokens: 0,
  cache_write_tokens: 0,
  cost_micros: 3_500_000,
}

const USAGE = {
  month: '2026-10',
  period: '2026-10-01',
  totals: { ...SUMS, classes: 2, revisions: 1, saves: 4 },
  by_plan: [
    { plan_id: 'free', classes: 1, reserved: 0, committed: 1, released: 1, refunded: 0, sums: SUMS },
    { plan_id: null, classes: 0, reserved: 0, committed: 0, released: 0, refunded: 0, sums: { ...SUMS, cost_micros: 300_000 } },
  ],
  by_kind_model: [{ job_kind: 'design_draft', provider: 'fake', model: 'fake-model', sums: SUMS }],
  jobs_by_kind: [{ kind: 'design_draft', completed: 2 }],
  top_users: [{ user_id: USER_ID, email: 'rider@example.test', runs: 3, cost_micros: 3_500_000, classes: 1 }],
  price_versions: [{ price_version: '2026-10-29', runs: 3 }],
  prices_version: '2026-10-29',
  prices: [
    {
      provider: 'anthropic',
      model: 'claude-opus-5-5',
      rates: { input: '4', output: '20', cache_write: '5', cache_read: '0.2' },
      above_tokens: 0,
      above: null,
    },
  ],
}

const JOBS = {
  days: 7,
  states: [
    { state: 'failed', count: 2 },
    { state: 'waiting_for_approval', count: 1 },
  ],
  waiting_approvals: [
    {
      user_id: USER_ID,
      email: 'rider@example.test',
      kind: 'design_with_songs',
      waiting_since: '2026-10-30T10:00:00Z',
      expires_at: '2026-11-06T10:00:00Z',
    },
  ],
  failures: [{ kind: 'design_draft', category: 'model_unavailable', count: 2, last_at: '2026-10-30T09:00:00Z' }],
  uncertain_operations: [
    { user_id: USER_ID, email: 'other@example.test', kind: 'spotify_create_playlist', since: '2026-10-30T08:00:00Z', job_state: null },
  ],
}

const AUDIT_ROW = {
  id: 'aaaaaaaa-0000-0000-0000-000000000001',
  created_at: '2026-10-31T09:00:00Z',
  actor_id: 'bbbbbbbb-0000-0000-0000-000000000002',
  actor_email: 'ann@example.test',
  action: 'refund.credit',
  target_user_id: USER_ID,
  target_kind: 'credit_ledger',
  target_id: 'cccccccc-0000-0000-0000-000000000003',
  params: {},
  outcome: 'refused',
  reason: 'already_refunded',
  request_id: 'req-1',
}

function adminApi(routes: Record<string, unknown> = {}) {
  return fakeApi({
    'GET /api/auth/session': ADMIN,
    'GET /api/admin/whoami': { email: 'ann@example.test' },
    ...(routes as Record<string, object>),
  })
}

function filter(values: Partial<AuditFilter>): AuditFilter {
  return { ...EMPTY_FILTER, ...values }
}

describe('admin usage', () => {
  it('shows the month asked for in the address, every amount labelled an estimate', async () => {
    const api = adminApi({ 'GET /api/admin/usage?month=2026-10': USAGE })
    renderAt('/admin/usage?month=2026-10')
    expect(await screen.findByRole('table', { name: 'Usage by plan' })).toBeInTheDocument()
    expect(screen.getByText('$3.50', { selector: 'dd' })).toBeInTheDocument()
    expect(screen.getByText(/estimates at list prices/)).toBeInTheDocument()
    expect(api.to('GET', '/api/admin/usage?month=2026-10')).toHaveLength(1)
    const byPlan = screen.getByRole('table', { name: 'Usage by plan' })
    expect(within(byPlan).getByRole('columnheader', { name: 'Estimated cost' })).toBeInTheDocument()
    expect(within(byPlan).getByText('No credit (revisions, saves, older jobs)')).toBeInTheDocument()
    expect(within(byPlan).getByText('$0.30')).toBeInTheDocument()
    for (const name of ['Usage by job kind and model', 'Top users by estimated cost']) {
      const table = screen.getByRole('table', { name })
      expect(within(table).getByRole('columnheader', { name: 'Estimated cost' })).toBeInTheDocument()
    }
    const top = screen.getByRole('table', { name: 'Top users by estimated cost' })
    expect(within(top).getByRole('link', { name: 'rider@example.test' })).toHaveAttribute('href', `/admin/users/${USER_ID}`)
    expect(screen.getByText(/Version 2026-10-29, read-only/)).toBeInTheDocument()
  })

  it('asks for the current month without a month and moves the month into the address', async () => {
    const api = adminApi({ 'GET /api/admin/usage': USAGE, 'GET /api/admin/usage?month=2026-09': { ...USAGE, month: '2026-09' } })
    renderAt('/admin/usage')
    const month = await screen.findByLabelText('Month (UTC)')
    await screen.findByRole('table', { name: 'Usage by plan' })
    fireEvent.change(month, { target: { value: '2026-09' } })
    await waitFor(() => {
      expect(api.to('GET', '/api/admin/usage?month=2026-09')).toHaveLength(1)
    })
    expect(window.location.search).toBe('?month=2026-09')
  })
})

describe('admin jobs', () => {
  it('shows another user’s waiting approval and uncertain operation, and the failure window', async () => {
    const api = adminApi({ 'GET /api/admin/jobs?days=7': JOBS, 'GET /api/admin/jobs?days=30': { ...JOBS, days: 30 } })
    renderAt('/admin/jobs')
    const waiting = await screen.findByRole('table', { name: 'Jobs waiting for approval' })
    expect(within(waiting).getByRole('link', { name: 'rider@example.test' })).toHaveAttribute('href', `/admin/users/${USER_ID}`)
    expect(within(waiting).getByText('design_with_songs')).toBeInTheDocument()
    const uncertain = screen.getByRole('table', { name: 'Uncertain external operations' })
    expect(within(uncertain).getByText('other@example.test')).toBeInTheDocument()
    expect(within(uncertain).getByText('Class deleted')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /resolve|check again/i })).toBeNull()
    const failures = screen.getByRole('table', { name: 'Failed jobs by kind and category' })
    expect(within(failures).getByText('model_unavailable')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Failures in the last'), { target: { value: '30' } })
    await waitFor(() => {
      expect(api.to('GET', '/api/admin/jobs?days=30')).toHaveLength(1)
    })
    expect(window.location.search).toBe('?days=30')
  })
})

describe('admin audit log', () => {
  it('reads its filters from the address and sends them to the API', async () => {
    const applied = filter({ action: 'user.', outcome: 'refused', since: '2026-10-01', until: '2026-10-31' })
    const api = adminApi({ [`GET ${auditPath(applied, '', null)}`]: { rows: [AUDIT_ROW], next_before: null } })
    renderAt('/admin/audit?action=user.&outcome=refused&since=2026-10-01&until=2026-10-31')
    const table = await screen.findByRole('table', { name: 'Audit log entries' })
    expect(within(table).getByText('already_refunded', { exact: false })).toBeInTheDocument()
    expect(api.to('GET', auditPath(applied, '', null))).toHaveLength(1)
    expect(screen.getByLabelText('Action')).toHaveValue('user.')
    expect(screen.getByLabelText('Outcome')).toHaveValue('refused')
    expect(screen.getByLabelText('From')).toHaveValue('2026-10-01')
    expect(screen.getByLabelText('To (inclusive)')).toHaveValue('2026-10-31')
  })

  it('puts a submitted filter in the address, keeps the email out of it, and comes back on Back', async () => {
    const empty = filter({})
    const next = filter({ action: 'refund.credit', targetKind: 'credit_ledger' })
    const api = adminApi({
      [`GET ${auditPath(empty, '', null)}`]: { rows: [], next_before: null },
      [`GET ${auditPath(next, 'ann@example.test', null)}`]: { rows: [AUDIT_ROW], next_before: null },
      [`GET ${auditPath(empty, 'ann@example.test', null)}`]: { rows: [], next_before: null },
    })
    renderAt('/admin/audit')
    expect(await screen.findByText('No entries match.')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Action'), { target: { value: 'refund.credit' } })
    fireEvent.change(screen.getByLabelText('Target kind'), { target: { value: 'credit_ledger' } })
    fireEvent.change(screen.getByLabelText('Admin email'), { target: { value: 'ann@example.test' } })
    fireEvent.click(screen.getByRole('button', { name: 'Filter' }))
    expect(await screen.findByRole('table', { name: 'Audit log entries' })).toBeInTheDocument()
    expect(window.location.search).toBe('?action=refund.credit&target_kind=credit_ledger')
    expect(window.location.href).not.toContain('ann%40example')
    expect(window.location.href).not.toContain('ann@example')
    expect(api.to('GET', auditPath(next, 'ann@example.test', null))).toHaveLength(1)
    act(() => {
      window.history.back()
    })
    await waitFor(() => {
      expect(screen.getByLabelText('Action')).toHaveValue('')
    })
    expect(window.location.search).toBe('')
    expect(await screen.findByText('No entries match.')).toBeInTheDocument()
  })

  it('pages with the cursor and back', async () => {
    const empty = filter({})
    const older = { ...AUDIT_ROW, id: 'aaaaaaaa-0000-0000-0000-000000000009', action: 'user.read' }
    adminApi({
      [`GET ${auditPath(empty, '', null)}`]: { rows: [AUDIT_ROW], next_before: AUDIT_ROW.id },
      [`GET ${auditPath(empty, '', AUDIT_ROW.id)}`]: { rows: [older], next_before: null },
    })
    renderAt('/admin/audit')
    fireEvent.click(await screen.findByRole('button', { name: 'Older' }))
    expect(await screen.findByText('user.read')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Older' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Newer' }))
    expect(await screen.findByText('refund.credit')).toBeInTheDocument()
  })

  it('shows a refused filter’s 422 message', async () => {
    const bad = filter({ since: '2026-10-31', until: '2026-10-01' })
    adminApi({ [`GET ${auditPath(bad, '', null)}`]: reason(422, 'period_invalid') })
    renderAt('/admin/audit?since=2026-10-31&until=2026-10-01')
    expect(await screen.findByText('The start of the period must be before its end.')).toBeInTheDocument()
  })
})

describe('admin navigation', () => {
  it('links the new pages', async () => {
    adminApi({ 'GET /api/admin/jobs?days=7': JOBS })
    renderAt('/admin/jobs')
    const nav = await screen.findByRole('navigation', { name: 'Admin' })
    for (const [name, to] of [
      ['Usage', '/admin/usage'],
      ['Jobs', '/admin/jobs'],
      ['Audit log', '/admin/audit'],
    ]) {
      expect(within(nav).getByRole('link', { name })).toHaveAttribute('href', to)
    }
    expect(within(nav).getByRole('link', { name: 'Jobs' })).toHaveAttribute('aria-current', 'page')
  })
})

describe('review follow-ups of #305', () => {
  it('sends the literal audit query: actor_id as typed and the end day as the start of the next day', async () => {
    const actor = 'bbbbbbbb-0000-0000-0000-000000000002'
    const since = encodeURIComponent(new Date(2026, 9, 1).toISOString())
    const until = encodeURIComponent(new Date(2026, 10, 1).toISOString()) // 31 October inclusive
    const path = `/api/admin/audit?action=refund.credit&actor_id=${actor}&since=${since}&until=${until}&limit=50`
    const api = adminApi({ [`GET ${path}`]: { rows: [AUDIT_ROW], next_before: null } })
    renderAt(`/admin/audit?action=refund.credit&actor_id=${actor}&since=2026-10-01&until=2026-10-31`)
    expect(await screen.findByRole('table', { name: 'Audit log entries' })).toBeInTheDocument()
    expect(api.to('GET', path)).toHaveLength(1)
  })

  it('labels the total an estimate and shows used credits by plan', async () => {
    adminApi({ 'GET /api/admin/usage?month=2026-10': USAGE })
    renderAt('/admin/usage?month=2026-10')
    const total = await screen.findByText('$3.50', { selector: 'dd' })
    expect(total.previousElementSibling).toHaveTextContent('Estimated cost')
    const byPlan = screen.getByRole('table', { name: 'Usage by plan' })
    const free = within(byPlan).getByRole('row', { name: /^Free/ })
    const headers = within(byPlan).getAllByRole('columnheader').map((h) => h.textContent)
    const cells = within(free).getAllByRole('cell').map((c) => c.textContent)
    expect(cells[headers.indexOf('Used')]).toBe('1')
  })

  it('keeps the Jobs window select when a load fails, and keeps its focus while loading', async () => {
    adminApi({
      'GET /api/admin/jobs?days=7': reason(500, 'internal'),
      'GET /api/admin/jobs?days=30': JOBS,
    })
    renderAt('/admin/jobs')
    expect(await screen.findByRole('alert')).toBeInTheDocument()
    const select = screen.getByLabelText('Failures in the last')
    select.focus()
    fireEvent.change(select, { target: { value: '30' } })
    expect(await screen.findByRole('table', { name: 'Jobs by state' })).toBeInTheDocument()
    expect(screen.getByLabelText('Failures in the last')).toBe(select)
    expect(document.activeElement).toBe(select)
  })

  it('drops the typed admin email after Back', async () => {
    const empty = filter({})
    const next = filter({ action: 'user.' })
    const api = adminApi({
      [`GET ${auditPath(empty, '', null)}`]: { rows: [], next_before: null },
      [`GET ${auditPath(next, 'ann@example.test', null)}`]: { rows: [AUDIT_ROW], next_before: null },
    })
    renderAt('/admin/audit')
    expect(await screen.findByText('No entries match.')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Action'), { target: { value: 'user.' } })
    fireEvent.change(screen.getByLabelText('Admin email'), { target: { value: 'ann@example.test' } })
    fireEvent.click(screen.getByRole('button', { name: 'Filter' }))
    expect(await screen.findByRole('table', { name: 'Audit log entries' })).toBeInTheDocument()
    act(() => {
      window.history.back()
    })
    await waitFor(() => {
      expect(screen.getByLabelText('Admin email')).toHaveValue('')
    })
    expect(await screen.findByText('No entries match.')).toBeInTheDocument()
    expect(api.calls.filter((c) => c.path.includes('actor_email'))).toHaveLength(1)
  })
})
