import { afterEach, describe, expect, it, vi } from 'vitest'
import { fakeApi, json } from '../test/fakeApi'
import { PreviewError, createPreviewSession, probeClip } from './previewClips'

const answer = (status: number, headers: Record<string, string> = {}, body: BodyInit | null = 'x') => new Response(body, { status, headers })
const ready = (start: string, end: string) => answer(206, { 'X-Preview-Start-Ms': start, 'X-Preview-End-Ms': end })

function stub(response: Response | { throws: Error | DOMException }) {
  const fetchMock = vi.fn(() => {
    if (response instanceof Response) return Promise.resolve(response)
    return Promise.reject(response.throws)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

afterEach(() => {
  vi.unstubAllGlobals()
})

const probe = (startMs = 0) => probeClip('c1', 's1', startMs, new AbortController().signal)

describe('probeClip', () => {
  it('asks for one byte and reads the window from the headers', async () => {
    const fetchMock = stub(ready('0', '240000'))
    const result = await probe()
    expect(result).toEqual({ kind: 'ready', clip: { url: '/api/classes/c1/preview/clip?session=s1&start_ms=0', startMs: 0, endMs: 240_000 } })
    expect(fetchMock).toHaveBeenCalledWith('/api/classes/c1/preview/clip?session=s1&start_ms=0', expect.objectContaining({ headers: { Range: 'bytes=0-0' } }))
  })

  it('accepts a 200 too (a server that ignored Range)', async () => {
    stub(answer(200, { 'X-Preview-Start-Ms': '5000', 'X-Preview-End-Ms': '245000' }))
    expect(await probe(5000)).toMatchObject({ kind: 'ready', clip: { startMs: 5000, endMs: 245_000 } })
  })

  it('says "rendering" for a 202 and clamps Retry-After to 1 to 10 seconds', async () => {
    stub(answer(202, { 'Retry-After': '0.2' }))
    expect(await probe()).toEqual({ kind: 'rendering', retryAfterMs: 1000 })
    stub(answer(202, { 'Retry-After': '120' }))
    expect(await probe()).toEqual({ kind: 'rendering', retryAfterMs: 10_000 })
    stub(answer(202))
    expect(await probe()).toEqual({ kind: 'rendering', retryAfterMs: 2000 })
  })

  it('refuses a window that does not start where it was asked or has no end', async () => {
    stub(ready('1000', '240000'))
    await expect(probe(0)).rejects.toMatchObject({ reason: 'failed' })
    stub(ready('0', '0'))
    await expect(probe(0)).rejects.toMatchObject({ reason: 'failed' })
    stub(answer(206))
    await expect(probe(0)).rejects.toMatchObject({ reason: 'failed' })
  })

  const statuses: [number, Record<string, unknown>, string][] = [
    [404, { detail: { reason: 'not_found' } }, 'unavailable'],
    [410, { detail: { reason: 'preview_session_ended' } }, 'session_ended'],
    [422, { detail: { reason: 'past_end' } }, 'past_end'],
    [429, { detail: { reason: 'rate_limited' } }, 'rate_limited'],
    [409, { detail: { reason: 'render_failed' } }, 'render_failed'],
    [409, { detail: { reason: 'music_changed' } }, 'draft_changed'],
    [409, { detail: { reason: 'no_studio_draft' } }, 'draft_changed'],
    [500, {}, 'failed'],
  ]
  for (const [status, body, failure] of statuses) {
    it(`maps ${String(status)} ${JSON.stringify(body)} to ${failure}`, async () => {
      stub(answer(status, { 'Content-Type': 'application/json' }, JSON.stringify(body)))
      await expect(probe()).rejects.toMatchObject({ reason: failure })
    })
  }

  it('maps an answer with no JSON to its status', async () => {
    stub(answer(410, {}, 'gone'))
    await expect(probe()).rejects.toMatchObject({ reason: 'session_ended' })
  })

  it('says "network" when the request fails and passes an abort through', async () => {
    stub({ throws: new TypeError('Failed to fetch') })
    await expect(probe()).rejects.toMatchObject({ reason: 'network' })
    const abort = new DOMException('Aborted', 'AbortError')
    stub({ throws: abort })
    await expect(probe()).rejects.toBe(abort)
  })
})

describe('createPreviewSession', () => {
  it('posts with no body and returns the session id', async () => {
    const api = fakeApi({ 'POST /api/classes/c1/preview/sessions': { session_id: 's9' } })
    expect(await createPreviewSession('c1')).toBe('s9')
    expect(api.to('POST', '/api/classes/c1/preview/sessions')).toHaveLength(1)
  })

  it('maps a 404, a 429, a 410 and an answer without the id', async () => {
    fakeApi({ 'POST /api/classes/c1/preview/sessions': () => json({ detail: { reason: 'not_found' } }, 404) })
    await expect(createPreviewSession('c1')).rejects.toMatchObject({ reason: 'unavailable' })
    fakeApi({ 'POST /api/classes/c1/preview/sessions': () => json({ detail: { reason: 'rate_limited' } }, 429) })
    await expect(createPreviewSession('c1')).rejects.toMatchObject({ reason: 'rate_limited' })
    fakeApi({ 'POST /api/classes/c1/preview/sessions': () => json({}) })
    await expect(createPreviewSession('c1')).rejects.toMatchObject({ reason: 'failed' })
  })

  it('says "network" when the request fails', async () => {
    fakeApi({})
    await expect(createPreviewSession('c1')).rejects.toBeInstanceOf(PreviewError)
  })
})
