import { ApiError, post } from './client'
import { isObject, text } from './decode'

// The audition's listening sessions and clips (ADR 0042 sections 6.3 to 6.5; backend
// `classes/preview_routes.py`). The browser reads only what the server returns: a window's start and
// end come from the answer, never from a placement of its own (`preview_window_calc` is the server's).

const base = (classId: string) => `/api/classes/${encodeURIComponent(classId)}/preview`

/** Why the audition could not give a session or a clip, as a code the page words (never shown raw). */
export type PreviewFailure =
  /** 410: the session's 30 minutes or 25 minutes of delivered audio are over. */
  | 'session_ended'
  /** 404: the audition is off here, or the session or class is not the user's. */
  | 'unavailable'
  | 'rate_limited'
  | 'render_failed'
  /** 409: the draft is no longer a studio draft the audition can play (its music changed). */
  | 'draft_changed'
  /** 422: the start is not inside the mix: the class is over. */
  | 'past_end'
  | 'network'
  | 'failed'

export class PreviewError extends Error {
  readonly reason: PreviewFailure

  constructor(reason: PreviewFailure) {
    super(reason)
    this.name = 'PreviewError'
    this.reason = reason
  }
}

/** One rendered window, in class milliseconds `[startMs, endMs)`, played from `url`. */
export interface Clip {
  url: string
  startMs: number
  endMs: number
}

export type ClipAnswer = { kind: 'ready'; clip: Clip } | { kind: 'rendering'; retryAfterMs: number }

const DEFAULT_RETRY_MS = 2000
const MIN_RETRY_MS = 1000
const MAX_RETRY_MS = 10_000

function failureOf(status: number, reason: string | null): PreviewFailure {
  if (status === 404) return 'unavailable'
  if (status === 410) return 'session_ended'
  if (status === 422) return 'past_end'
  if (status === 429) return 'rate_limited'
  if (status === 409) {
    if (reason === 'render_failed') return 'render_failed'
    return reason === 'no_studio_draft' || reason === 'music_changed' ? 'draft_changed' : 'failed'
  }
  return 'failed'
}

function detailReason(body: unknown): string | null {
  if (!isObject(body) || !isObject(body.detail)) return null
  return typeof body.detail.reason === 'string' ? body.detail.reason : null
}

export function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError'
}

/** The failure code of anything the transport's service threw. */
export const reasonOf = (error: unknown): PreviewFailure => (error instanceof PreviewError ? error.reason : 'failed')

/** The Play gesture: a new listening session, `POST .../preview/sessions` with no body. */
export async function createPreviewSession(classId: string): Promise<string> {
  let body: unknown
  try {
    body = await post(`${base(classId)}/sessions`)
  } catch (error) {
    if (error instanceof ApiError) throw new PreviewError(failureOf(error.status, error.reason))
    throw new PreviewError('network')
  }
  if (!isObject(body)) throw new PreviewError('failed')
  try {
    return text(body, 'session_id')
  } catch {
    throw new PreviewError('failed')
  }
}

export const clipUrl = (classId: string, sessionId: string, startMs: number): string =>
  `${base(classId)}/clip?session=${encodeURIComponent(sessionId)}&start_ms=${String(startMs)}`

function headerMs(response: Response, name: string): number | null {
  const raw = response.headers.get(name)
  return raw !== null && /^\d+$/.test(raw) ? Number(raw) : null
}

/**
 * Asks for the clip that starts at `startMs`. An `audio` element cannot read a 202 "rendering"
 * answer, so the browser first asks with `Range: bytes=0-0`: a ready clip answers its window in
 * `X-Preview-Start-Ms` and `X-Preview-End-Ms` and one byte (the element then reads the same URL, and
 * the server counts a clip once per session however many ranges it is read with); a clip still
 * rendering answers 202 and the caller asks again. A request that was aborted throws the abort.
 */
export async function probeClip(
  classId: string,
  sessionId: string,
  startMs: number,
  signal: AbortSignal,
): Promise<ClipAnswer> {
  const url = clipUrl(classId, sessionId, startMs)
  let response: Response
  try {
    response = await fetch(url, { headers: { Range: 'bytes=0-0' }, signal })
  } catch (error) {
    if (isAbort(error) || signal.aborted) throw error
    throw new PreviewError('network')
  }
  if (response.status === 202) {
    const wait = Number(response.headers.get('Retry-After')) * 1000
    void response.body?.cancel().catch(() => undefined)
    const retryAfterMs = Number.isFinite(wait) && wait > 0 ? Math.min(MAX_RETRY_MS, Math.max(MIN_RETRY_MS, wait)) : DEFAULT_RETRY_MS
    return { kind: 'rendering', retryAfterMs }
  }
  if (response.status === 200 || response.status === 206) {
    const start = headerMs(response, 'X-Preview-Start-Ms')
    const end = headerMs(response, 'X-Preview-End-Ms')
    void response.body?.cancel().catch(() => undefined) // one byte was asked for; a server that ignored Range sends the file
    if (start !== startMs || end === null || end <= start) throw new PreviewError('failed')
    return { kind: 'ready', clip: { url, startMs: start, endMs: end } }
  }
  let reason: string | null = null
  try {
    reason = detailReason((await response.json()) as unknown)
  } catch {
    // No JSON detail: the status says enough.
  }
  throw new PreviewError(failureOf(response.status, reason))
}
