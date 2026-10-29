import { ApiError } from './client'

// The job's progress as server-sent events (ADR 0005 section 6). Read with `fetch` rather than
// `EventSource`: the page needs the HTTP status (404, 429) and must decide itself when to stop
// reconnecting. A reconnect after a terminal event gets a 204 (#116 item 4): nothing more will
// come, so it counts as an ended stream. The caller also stops after an event that ends the
// stream and checks the job state before it opens another.

export interface JobEvent {
  seq: number
  kind: string
  /** Enum values, counts, ids and timestamps only (the server validates them). */
  payload: Record<string, unknown>
}

/** Events after which the server ends the stream: the job is over or waits for the user. */
const STREAM_ENDING_KINDS: readonly string[] = [
  'completed',
  'failed',
  'cancelled',
  'expired',
  'waiting_for_approval',
]

function parseFrame(block: string): JobEvent | null {
  let id: string | null = null
  let kind: string | null = null
  const data: string[] = []
  for (const line of block.split(/\r?\n/)) {
    if (line.startsWith(':')) continue // a keepalive comment
    const colon = line.indexOf(':')
    const field = colon === -1 ? line : line.slice(0, colon)
    const value = colon === -1 ? '' : line.slice(colon + 1).replace(/^ /, '')
    if (field === 'id') id = value
    else if (field === 'event') kind = value
    else if (field === 'data') data.push(value)
  }
  if (id === null || kind === null || !/^\d+$/.test(id)) return null
  let payload: Record<string, unknown> = {}
  try {
    const parsed: unknown = JSON.parse(data.join('\n') || '{}')
    if (typeof parsed === 'object' && parsed !== null && !Array.isArray(parsed)) {
      payload = parsed as Record<string, unknown>
    }
  } catch {
    // An unreadable payload is shown as an event without details.
  }
  return { seq: Number(id), kind, payload }
}

/**
 * Reads one stream connection from the event after `after` until the server closes it, calling
 * `onEvent` per event. Resolves to whether an event that ends the stream arrived (or the server
 * answered 204: the job is over and nothing is left to send). Throws an
 * `ApiError` for a non-2xx answer and rethrows an abort.
 */
export async function readJobEvents(
  jobId: string,
  after: number,
  signal: AbortSignal,
  onEvent: (event: JobEvent) => void,
): Promise<boolean> {
  const headers: Record<string, string> = { Accept: 'text/event-stream' }
  if (after > 0) headers['Last-Event-ID'] = String(after)
  const response = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/events`, {
    headers,
    signal,
  })
  if (!response.ok) {
    const retry = Number(response.headers.get('Retry-After'))
    throw new ApiError(
      response.status,
      null,
      Number.isFinite(retry) && retry > 0 ? retry : null,
      undefined,
    )
  }
  if (response.status === 204) return true // the job is over and every event was seen
  if (response.body === null) return false
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let ended = false
  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const blocks = buffer.split(/\r?\n\r?\n/)
      buffer = blocks.pop() ?? ''
      for (const block of blocks) {
        const event = parseFrame(block)
        if (event === null) continue
        onEvent(event)
        if (STREAM_ENDING_KINDS.includes(event.kind)) ended = true
      }
    }
  } finally {
    await reader.cancel().catch(() => undefined)
  }
  return ended
}
