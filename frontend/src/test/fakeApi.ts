import { vi } from 'vitest'
import { rememberCsrfToken } from '../api/client'

export type Handler = (body: unknown, headers: Headers) => unknown
export interface Call {
  method: string
  path: string
  body: unknown
  headers: Headers
}

export function json(body: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json', ...headers },
  })
}

export function reason(status: number, why: string): Response {
  return json({ detail: { reason: why } }, status)
}

export const USAGE_NONE = {
  month: '2026-10',
  total: { runs: 0, jobs: 0, input_tokens: 0, output_tokens: 0, cache_read_tokens: 0, cache_write_tokens: 0, cost_micros: 0 },
  by_kind_model: [],
  classes_designed: 0,
  revisions: 0,
  notes: [],
}

export const PLAN_FREE = {
  plan: 'free',
  name: 'Free',
  source: null,
  ends_at: null,
  limits: { classes_per_month: 3, revisions_per_class: 10, concurrent_jobs: 1 },
  classes_held: 1,
  period_end: '2026-11-01T00:00:00Z',
  cost_share: 0.25,
  features: [],
  trial: null,
}

const ANONYMOUS = { authenticated: false, csrf_token: 'csrf-anon' }

export const SIGNED_IN = {
  authenticated: true,
  csrf_token: 'csrf-session',
  user: { id: 'u1', email: 'ann@example.test', role: 'user' },
  session: { id: 's1', expires_at: '2030-01-01T00:00:00Z', recent_auth: true, mfa: false, stepup: false },
}

/**
 * Stubs `fetch` with a table of `"METHOD /path"` handlers. A handler returns a Response or any
 * JSON value (answered 200); a missing route fails the test loudly. `/api/auth/session`
 * defaults to anonymous, `/api/connections` and `/api/external-operations` to none, `/api/connections/spotify` to
 * 404 `feature_off` and
 * `/api/usage` to an empty month.
 */
export function fakeApi(routes: Record<string, Handler | object | string>) {
  rememberCsrfToken(null)
  const calls: Call[] = []
  const table: Record<string, Handler | object | string> = {
    'GET /api/auth/session': ANONYMOUS,
    'GET /api/connections': { connections: [] },
    'GET /api/connections/spotify': () => json({ detail: { reason: 'feature_off' } }, 404),
    'GET /api/external-operations': { operations: [] },
    'GET /api/usage': USAGE_NONE,
    'GET /api/account/plan': PLAN_FREE,
    'GET /api/settings/public': { banner: null, signup_mode: 'invite' },
    ...routes,
  }
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const path = typeof input === 'string' ? input : input instanceof URL ? input.pathname : input.url
    const method = init?.method ?? 'GET'
    const headers = new Headers(init?.headers)
    const body = typeof init?.body === 'string' ? (JSON.parse(init.body) as unknown) : undefined
    calls.push({ method, path, body, headers })
    const route = table[`${method} ${path}`]
    if (route === undefined) {
      return Promise.reject(new Error(`unexpected request: ${method} ${path}`))
    }
    const answer = typeof route === 'function' ? (route as Handler)(body, headers) : route
    return Promise.resolve(answer instanceof Response ? answer.clone() : json(answer))
  })
  vi.stubGlobal('fetch', fetchMock)
  return {
    calls,
    to: (method: string, path: string) =>
      calls.filter((call) => call.method === method && call.path === path),
  }
}
