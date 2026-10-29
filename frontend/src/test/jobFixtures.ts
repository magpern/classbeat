import { SIGNED_IN } from './fakeApi'

export const JOB_ID = '11111111-1111-4111-8111-111111111111'
export const CLASS_ID = '22222222-2222-4222-8222-222222222222'
export const REVISION_ID = '33333333-3333-4333-8333-333333333333'
export const OPERATION_ID = '44444444-4444-4444-8444-444444444444'
export const PLAYLIST_ID = 'AbCdEfGhIjKlMnOpQrStUv'

export const SESSION_ROUTES = { 'GET /api/auth/session': SIGNED_IN }

/** A `GET /api/jobs/{id}` answer. */
export function jobBody(state: string, extra: Record<string, unknown> = {}) {
  return {
    id: JOB_ID,
    kind: 'design_draft',
    state,
    stage: null,
    class_id: CLASS_ID,
    cancel_requested: false,
    approval_revision_id: null,
    expires_at: null,
    approved_at: null,
    error_category: null,
    created_at: '2026-10-29T10:00:00Z',
    updated_at: '2026-10-29T10:00:00Z',
    finished_at: null,
    ...extra,
  }
}

export function sse(...events: [number, string, Record<string, unknown>?][]): Response {
  const text = events
    .map(([seq, kind, payload]) => `id: ${String(seq)}\nevent: ${kind}\ndata: ${JSON.stringify(payload ?? {})}\n\n`)
    .join('')
  return new Response(`: keepalive\n\n${text}`, { headers: { 'Content-Type': 'text/event-stream' } })
}

/** A stream that stays open and silent, like a job that is still running. */
export function openStream(): Response {
  return new Response(new ReadableStream({ start: () => undefined }), {
    headers: { 'Content-Type': 'text/event-stream' },
  })
}

export const DRAFT = {
  revision_id: REVISION_ID,
  number: 1,
  kind: 'draft',
  content: {
    name: 'Tuesday climb <b>bold</b>',
    description: 'Two hills <img src=x onerror=alert(1)>',
    segments: [
      {
        name: 'Warm up',
        kind: 'warmup',
        intervals: [
          { sec: 300, zone: 2, rpm: [80, 90], standing: false, cue: 'Easy now' },
          { sec: 90, zone: 5, rpm: 'beat', standing: true, cue: '' },
        ],
      },
    ],
  },
  unknown_bpm: [{ index: 2, title: 'Song <i>without</i> BPM' }],
  created_at: '2026-10-29T10:05:00Z',
}
