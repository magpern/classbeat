import { afterEach, describe, expect, it, vi } from 'vitest'
import { fakeApi, json, reason } from '../test/fakeApi'
import { ApiError, del, fetchSessionJson, get, post } from './client'
import { errorMessage } from './messages'

describe('API client', () => {
  it('sends no CSRF header on GET', async () => {
    const api = fakeApi({ 'GET /api/x': { ok: true } })
    await get('/api/x')
    expect(api.calls[0]?.headers.get('X-CSRF-Token')).toBeNull()
  })

  it('fetches the CSRF token first and sends it on POST', async () => {
    const api = fakeApi({ 'POST /api/x': { ok: true } })
    await post('/api/x', { a: 1 })
    expect(api.calls.map((c) => `${c.method} ${c.path}`)).toEqual([
      'GET /api/auth/session',
      'POST /api/x',
    ])
    expect(api.calls[1]?.headers.get('X-CSRF-Token')).toBe('csrf-anon')
    expect(api.calls[1]?.body).toEqual({ a: 1 })
  })

  it('fetches a new token after a successful unsafe request (the session may have rotated)', async () => {
    const api = fakeApi({ 'POST /api/x': { ok: true } })
    await post('/api/x')
    await post('/api/x')
    expect(api.to('GET', '/api/auth/session')).toHaveLength(2)
  })

  it('retries once with a fresh token when the server blames the CSRF token', async () => {
    let tokens = 0
    let posts = 0
    const api = fakeApi({
      'GET /api/auth/session': () => ({ authenticated: false, csrf_token: `t${String(++tokens)}` }),
      'POST /api/x': (_body: unknown, headers: Headers) =>
        ++posts === 1 ? reason(403, 'csrf_token_invalid') : { seen: headers.get('X-CSRF-Token') },
    })
    const result = await post('/api/x')
    expect(result).toEqual({ seen: 't2' })
    expect(api.to('POST', '/api/x')).toHaveLength(2)
  })

  it('does not retry twice', async () => {
    const api = fakeApi({ 'POST /api/x': reason(403, 'csrf_token_invalid') })
    await expect(post('/api/x')).rejects.toMatchObject({ status: 403, reason: 'csrf_token_invalid' })
    expect(api.to('POST', '/api/x')).toHaveLength(2)
  })

  it('does not retry other 403 answers', async () => {
    const api = fakeApi({ 'POST /api/x': reason(403, 'reauth_required') })
    await expect(post('/api/x')).rejects.toMatchObject({ reason: 'reauth_required' })
    expect(api.to('POST', '/api/x')).toHaveLength(1)
  })

  it('throws ApiError with status, reason and Retry-After', async () => {
    fakeApi({ 'GET /api/x': json({ detail: { reason: 'rate_limited' } }, 429, { 'Retry-After': '120' }) })
    const error = await get('/api/x').catch((e: unknown) => e)
    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({ status: 429, reason: 'rate_limited', retryAfter: 120 })
    expect(errorMessage(error)).toBe('Too many attempts. Try again in 2 minutes.')
  })

  describe('a busy answer to the session read (#998, #1567)', () => {
    const busy = (retryAfter?: string) =>
      json({ detail: { reason: 'busy' } }, 503, retryAfter === undefined ? {} : { 'Retry-After': retryAfter })
    const OK = { authenticated: false, csrf_token: 'csrf-after-busy' }
    const reads = (api: ReturnType<typeof fakeApi>) => api.to('GET', '/api/auth/session').length

    afterEach(() => {
      vi.useRealTimers()
    })

    it('waits for Retry-After and reads the session again', async () => {
      vi.useFakeTimers()
      let n = 0
      const api = fakeApi({ 'GET /api/auth/session': () => (++n === 1 ? busy('2') : OK) })
      const done = fetchSessionJson()
      await vi.advanceTimersByTimeAsync(1999)
      expect(reads(api)).toBe(1)
      await vi.advanceTimersByTimeAsync(1)
      expect(await done).toMatchObject({ csrf_token: 'csrf-after-busy' })
      expect(reads(api)).toBe(2)
    })

    it('caps the wait at 5 seconds however large Retry-After is', async () => {
      vi.useFakeTimers()
      let n = 0
      const api = fakeApi({ 'GET /api/auth/session': () => (++n === 1 ? busy('3600') : OK) })
      const done = fetchSessionJson()
      await vi.advanceTimersByTimeAsync(4999)
      expect(reads(api)).toBe(1)
      await vi.advanceTimersByTimeAsync(1)
      await done
      expect(reads(api)).toBe(2)
    })

    it.each([
      ['absent', undefined],
      ['garbage', 'soon'],
    ])('waits 1 second when Retry-After is %s', async (_name, header) => {
      vi.useFakeTimers()
      let n = 0
      const api = fakeApi({ 'GET /api/auth/session': () => (++n === 1 ? busy(header) : OK) })
      const done = fetchSessionJson()
      await vi.advanceTimersByTimeAsync(999)
      expect(reads(api)).toBe(1)
      await vi.advanceTimersByTimeAsync(1)
      await done
      expect(reads(api)).toBe(2)
    })

    it('gives up after two repeats and reports the 503', async () => {
      vi.useFakeTimers()
      const api = fakeApi({ 'GET /api/auth/session': busy('1') })
      const done = fetchSessionJson().catch((e: unknown) => e)
      await vi.advanceTimersByTimeAsync(2000)
      expect(await done).toMatchObject({ status: 503, reason: 'busy' })
      expect(reads(api)).toBe(3)
    })

    it.each([
      ['another 503', reason(503, 'down'), 503, 'down'],
      ['a 401', reason(401, 'not_authenticated'), 401, 'not_authenticated'],
      ['a 403', reason(403, 'forbidden'), 403, 'forbidden'],
    ])('does not repeat %s', async (_name, answer, status, why) => {
      const api = fakeApi({ 'GET /api/auth/session': answer })
      await expect(fetchSessionJson()).rejects.toMatchObject({ status, reason: why })
      expect(reads(api)).toBe(1)
    })

    it('does not repeat a network error', async () => {
      const api = fakeApi({ 'GET /api/auth/session': () => Promise.reject(new TypeError('Failed to fetch')) })
      await expect(fetchSessionJson()).rejects.toThrow('Failed to fetch')
      expect(reads(api)).toBe(1)
    })

    it('ends the wait at once when the signal aborts, and reads no more', async () => {
      vi.useFakeTimers()
      const api = fakeApi({ 'GET /api/auth/session': busy('5') })
      const controller = new AbortController()
      const done = fetchSessionJson(controller.signal).catch((e: unknown) => e)
      await vi.advanceTimersByTimeAsync(0)
      controller.abort()
      expect(await done).toMatchObject({ name: 'AbortError' })
      expect(reads(api)).toBe(1)
      expect(vi.getTimerCount()).toBe(0)
    })

    it('sends an unsafe request whose token read was repeated, after another request used and dropped its token', async () => {
      vi.useFakeTimers()
      let n = 0
      const api = fakeApi({
        'GET /api/auth/session': () =>
          ++n === 1 ? busy('1') : { authenticated: false, csrf_token: 'csrf-same-session' },
        'POST /api/a': { ok: 'a' },
        'POST /api/b': { ok: 'b' },
      })
      const a = post('/api/a') // reads the token, is told busy, waits a second
      await vi.advanceTimersByTimeAsync(0)
      expect(await post('/api/b')).toEqual({ ok: 'b' }) // its own read, sent, token dropped
      await vi.advanceTimersByTimeAsync(1000)
      expect(await a).toEqual({ ok: 'a' })
      expect(api.to('POST', '/api/a')[0]?.headers.get('X-CSRF-Token')).toBe('csrf-same-session')
    })
  })

  it('uses the token its own read returned even when another request drops the token meanwhile', async () => {
    let releaseRead: (answer: unknown) => void = () => undefined
    let releaseB: (answer: unknown) => void = () => undefined
    let reads = 0
    const api = fakeApi({
      'GET /api/auth/session': () =>
        ++reads === 1
          ? new Promise((resolve) => (releaseRead = resolve)) // A's read, answered late
          : { authenticated: false, csrf_token: 'csrf-b' },
      'POST /api/a': { ok: 'a' },
      'POST /api/b': () => new Promise((resolve) => (releaseB = resolve)),
    })
    const a = post('/api/a')
    const b = post('/api/b')
    await vi.waitFor(() => expect(api.to('POST', '/api/b')).toHaveLength(1))
    // A's read answers and B succeeds (dropping the remembered token) in the same turn.
    releaseRead({ authenticated: false, csrf_token: 'csrf-a' })
    releaseB({ ok: 'b' })
    expect(await b).toEqual({ ok: 'b' })
    expect(await a).toEqual({ ok: 'a' })
    expect(api.to('POST', '/api/a')[0]?.headers.get('X-CSRF-Token')).toBe('csrf-a')
  })

  describe('401 after the session rotated (ADR 0003 section 3)', () => {
    it('repeats a GET once after refetching the session', async () => {
      let gets = 0
      const api = fakeApi({
        'GET /api/x': () => (++gets === 1 ? reason(401, 'not_authenticated') : { ok: true }),
      })
      expect(await get('/api/x')).toEqual({ ok: true })
      expect(api.to('GET', '/api/x')).toHaveLength(2)
      expect(api.to('GET', '/api/auth/session')).toHaveLength(1)
    })

    it('gives up after one repeat', async () => {
      const api = fakeApi({ 'GET /api/x': reason(401, 'not_authenticated') })
      await expect(get('/api/x')).rejects.toMatchObject({ status: 401 })
      expect(api.to('GET', '/api/x')).toHaveLength(2)
    })

    it.each([
      ['/api/auth/mfa/stepup', 'not_authenticated'],
      ['/api/auth/reauth', 'not_authenticated'],
      ['/api/auth/login', 'invalid_credentials'],
    ])('sends a POST to %s exactly once when it gets 401 %s', async (path, why) => {
      const api = fakeApi({ [`POST ${path}`]: reason(401, why) })
      await expect(post(path)).rejects.toMatchObject({ status: 401, reason: why })
      expect(api.to('POST', path)).toHaveLength(1)
    })

    it('never repeats a DELETE or a bodyless 401 either', async () => {
      const api = fakeApi({ 'DELETE /api/x': json({}, 401) })
      await expect(del('/api/x')).rejects.toMatchObject({ status: 401 })
      expect(api.to('DELETE', '/api/x')).toHaveLength(1)
    })

    it('reports the original 401 when the session cannot be refetched', async () => {
      const api = fakeApi({
        'GET /api/x': reason(401, 'not_authenticated'),
        'GET /api/auth/session': reason(500, 'boom'),
      })
      await expect(get('/api/x')).rejects.toMatchObject({ status: 401, reason: 'not_authenticated' })
      expect(api.to('GET', '/api/x')).toHaveLength(1)
    })
  })

  it('returns undefined for an empty 204 answer', async () => {
    fakeApi({ 'POST /api/x': new Response(null, { status: 204 }) })
    expect(await post('/api/x')).toBeUndefined()
  })

  it('turns reasons into plain language and hides unknown codes', () => {
    expect(errorMessage(new ApiError(400, 'invalid_invite', null, null))).toMatch(/invite code/)
    expect(errorMessage(new ApiError(400, 'something_new', null, null))).not.toMatch(/something_new/)
    expect(errorMessage(new TypeError('Failed to fetch'))).toMatch(/could not be reached/)
  })
})
