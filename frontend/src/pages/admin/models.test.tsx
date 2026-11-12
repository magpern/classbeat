import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { SIGNED_IN, fakeApi, reason } from '../../test/fakeApi'
import { renderAt } from '../../test/render'

// The admin Model routes screen: the response shapes of backend/src/classbeat/admin/models.py.
// Each plan shows its quality word and route key, the model and effort each stage maps to, and a
// form that sets a route; a model without a passing eval is refused in words and nothing changes.

const ADMIN = { ...SIGNED_IN, user: { ...SIGNED_IN.user, role: 'admin' } }
const MODELS = '/api/admin/models'
const ROUTE = `${MODELS}/routes/instructor/design`
const STEPUP_CODE = '424242'

const RESULT = {
  id: '11111111-0000-0000-0000-000000000001',
  run_id: '11111111-0000-0000-0000-000000000002',
  recorded_at: '2026-11-01T08:00:00Z',
  suite_version: 12,
  provider: 'anthropic',
  model: 'claude-sonnet-x',
  effort: 'medium',
  stage_group: 'design',
  passed: true,
  status: 'passed',
  total_cost_micros: 1000,
  classbeat_version: '1.0.0',
  authorization_note: '',
  current: true,
}

function route(overrides: Record<string, unknown> = {}) {
  return {
    route_key: 'instructor',
    stage_group: 'design',
    provider: 'anthropic',
    model: 'claude-sonnet-x',
    effort: 'medium',
    set_at: '2026-11-01T09:00:00Z',
    set_by: 'ann@example.test',
    stale: [],
    refused_by_worker: false,
    result: RESULT,
    ...overrides,
  }
}

function view(routes: unknown[] = [route()]) {
  return {
    plans: [
      { id: 'free', name: 'Free', rank: 0, route_key: 'free', quality: 'standard' },
      { id: 'instructor', name: 'Instructor', rank: 1, route_key: 'instructor', quality: 'better' },
      { id: 'pro', name: 'Pro', rank: 2, route_key: 'pro', quality: 'best' },
    ],
    routes,
    results: [RESULT],
    catalog: [
      { provider: 'anthropic', model: 'claude-sonnet-x', efforts: ['low', 'medium'], data_policy: 'allowed', enabled: true },
      { provider: 'anthropic', model: 'claude-opus-x', efforts: ['high'], data_policy: 'allowed', enabled: true },
      { provider: 'deepseek', model: 'deep-x', efforts: ['high'], data_policy: 'allowed', enabled: false },
    ],
  }
}

function adminApi(routes: Record<string, unknown>) {
  return fakeApi({
    'GET /api/auth/session': ADMIN,
    'GET /api/admin/whoami': { email: 'ann@example.test' },
    ...(routes as Record<string, object>),
  })
}

function group(name: string): HTMLElement {
  return screen.getByRole('group', { name })
}

describe('admin model routes', () => {
  it('lists each plan with its quality word, route key and mapped model and effort', async () => {
    adminApi({ [`GET ${MODELS}`]: view() })
    renderAt('/admin/models')
    expect(await screen.findByRole('heading', { name: 'Model routes' })).toBeInTheDocument()
    expect(await screen.findByTestId('quality-free')).toHaveTextContent('Standard')
    expect(screen.getByTestId('quality-instructor')).toHaveTextContent('Better')
    expect(screen.getByTestId('quality-pro')).toHaveTextContent('Best')
    const instructor = screen.getByRole('region', { name: 'Instructor' })
    expect(within(instructor).getByText('instructor', { selector: 'code' })).toBeInTheDocument()
    expect(screen.getByTestId('route-instructor-design')).toHaveTextContent('anthropic / claude-sonnet-x at effort medium')
    expect(within(group('Instructor song picking')).getByText(/No route is set/)).toBeInTheDocument()
    expect(within(group('Free design')).getByText(/No route is set/)).toBeInTheDocument()
  })

  it('has a Models link in the admin navigation', async () => {
    adminApi({ [`GET ${MODELS}`]: view() })
    renderAt('/admin/models')
    const link = await screen.findByRole('link', { name: 'Models' })
    expect(link).toHaveAttribute('aria-current', 'page')
  })

  it('marks a route stale and says when the worker refuses it', async () => {
    adminApi({
      [`GET ${MODELS}`]: view([
        route({ stale: ['suite_version'] }),
        route({ stage_group: 'songs', stale: ['newer_failure'], refused_by_worker: true }),
      ]),
    })
    renderAt('/admin/models')
    const design = await screen.findByRole('group', { name: 'Instructor design' })
    expect(within(design).getByText('Stale')).toBeInTheDocument()
    expect(within(design).getByText(/eval suite is newer/)).toBeInTheDocument()
    expect(within(group('Instructor song picking')).getByText(/worker refuses this route/)).toBeInTheDocument()
  })

  it('offers a catalog entry the server would refuse as disabled', async () => {
    adminApi({ [`GET ${MODELS}`]: view() })
    renderAt('/admin/models')
    const form = await screen.findByRole('form', { name: 'Set the Instructor design route' })
    expect(within(form).getByRole('option', { name: 'deepseek / deep-x (not allowed)' })).toBeDisabled()
  })

  it('sets a route through the step-up and shows the new mapping', async () => {
    let stepped = false
    let stored = view().routes
    const api = adminApi({
      [`GET ${MODELS}`]: () => view(stored),
      [`PUT ${ROUTE}`]: () => {
        if (!stepped) return reason(403, 'stepup_required')
        stored = [route({ model: 'claude-opus-x', effort: 'high' })]
        return stored[0]
      },
      'POST /api/auth/mfa/stepup': () => {
        stepped = true
        return { ok: true }
      },
    })
    renderAt('/admin/models')
    const form = await screen.findByRole('form', { name: 'Set the Instructor design route' })
    fireEvent.change(within(form).getByLabelText('Model'), { target: { value: '1' } })
    expect(within(form).getByLabelText('Effort')).toHaveValue('high')
    fireEvent.click(within(form).getByRole('button', { name: 'Set route' }))
    const dialog = await screen.findByRole('dialog', { name: 'Enter a two-factor code' })
    fireEvent.change(within(dialog).getByLabelText('Authenticator or recovery code'), { target: { value: STEPUP_CODE } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Continue' }))
    await waitFor(() => {
      expect(screen.getByTestId('route-instructor-design')).toHaveTextContent('claude-opus-x at effort high')
    })
    expect(api.to('PUT', ROUTE).map((c) => c.body)).toEqual([
      { provider: 'anthropic', model: 'claude-opus-x', effort: 'high' },
      { provider: 'anthropic', model: 'claude-opus-x', effort: 'high' },
    ])
    expect(screen.getByText('The route is set.')).toBeInTheDocument()
  })

  it('shows the refusal of a model without a passing eval and keeps the route', async () => {
    adminApi({
      [`GET ${MODELS}`]: view(),
      [`PUT ${ROUTE}`]: reason(409, 'no_result'),
    })
    renderAt('/admin/models')
    const form = await screen.findByRole('form', { name: 'Set the Instructor design route' })
    fireEvent.change(within(form).getByLabelText('Model'), { target: { value: '1' } })
    fireEvent.click(within(form).getByRole('button', { name: 'Set route' }))
    const alert = await within(group('Instructor design')).findByRole('alert')
    expect(alert).toHaveTextContent('Refused, the route is unchanged.')
    expect(alert).toHaveTextContent('has not passed the eval suite')
    expect(screen.getByTestId('route-instructor-design')).toHaveTextContent('claude-sonnet-x at effort medium')
    expect(screen.queryByText('The route is set.')).not.toBeInTheDocument()
  })

  it('shows a load failure in words', async () => {
    adminApi({ [`GET ${MODELS}`]: reason(503, 'admin_unavailable') })
    renderAt('/admin/models')
    expect(await screen.findByText('The admin area is not available right now.')).toBeInTheDocument()
  })
})
