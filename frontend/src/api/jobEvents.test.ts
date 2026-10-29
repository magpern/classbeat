import { describe, expect, it, vi } from 'vitest'
import { ApiError } from './client'
import { readJobEvents, type JobEvent } from './jobEvents'

function chunks(...parts: string[]): Response {
  const encoder = new TextEncoder()
  return new Response(
    new ReadableStream({
      start(controller) {
        for (const part of parts) controller.enqueue(encoder.encode(part))
        controller.close()
      },
    }),
  )
}

async function read(response: Response, after = 0) {
  const fetchMock = vi.fn(() => Promise.resolve(response))
  vi.stubGlobal('fetch', fetchMock)
  const events: JobEvent[] = []
  const ended = await readJobEvents('job', after, new AbortController().signal, (e) => events.push(e))
  return { events, ended, fetchMock }
}

describe('readJobEvents', () => {
  it('reads frames split across chunks, skips keepalive comments and tolerates CRLF', async () => {
    const { events, ended } = await read(
      chunks(': keepalive\n\nid: 1\nevent: que', 'ued\ndata: {}\n\nid: 2\r\nevent: claimed\r\ndata: {"attempt":1}\r\n\r\n'),
    )
    expect(events).toEqual([
      { seq: 1, kind: 'queued', payload: {} },
      { seq: 2, kind: 'claimed', payload: { attempt: 1 } },
    ])
    expect(ended).toBe(false)
  })

  it('reports an event that ends the stream', async () => {
    const { ended } = await read(chunks('id: 5\nevent: completed\ndata: {}\n\n'))
    expect(ended).toBe(true)
  })

  it('sends Last-Event-ID only after the first event', async () => {
    const first = await read(chunks(''))
    expect(first.fetchMock).toHaveBeenCalledWith(
      '/api/jobs/job/events',
      expect.objectContaining({ headers: { Accept: 'text/event-stream' } }),
    )
    const later = await read(chunks(''), 7)
    expect(later.fetchMock).toHaveBeenCalledWith(
      '/api/jobs/job/events',
      expect.objectContaining({ headers: { Accept: 'text/event-stream', 'Last-Event-ID': '7' } }),
    )
  })

  it('treats an empty 200 after the end as no events, not as an error', async () => {
    const { events, ended } = await read(new Response('', { status: 200 }), 9)
    expect(events).toEqual([])
    expect(ended).toBe(false)
  })

  it('ignores frames without an id or event and unreadable payloads become empty', async () => {
    const { events } = await read(chunks('event: x\ndata: {}\n\nid: 3\nevent: claimed\ndata: not json\n\n'))
    expect(events).toEqual([{ seq: 3, kind: 'claimed', payload: {} }])
  })

  it('throws an ApiError with the status and Retry-After of a refusal', async () => {
    const refusal = new Response('{}', { status: 429, headers: { 'Retry-After': '5' } })
    await expect(read(refusal)).rejects.toMatchObject({ status: 429, retryAfter: 5 })
    await expect(read(new Response('{}', { status: 404 }))).rejects.toBeInstanceOf(ApiError)
  })
})
