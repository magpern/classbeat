import { CLASS_ID } from './jobFixtures'

export const REV1 = 'a1111111-1111-4111-8111-111111111111'
export const REV2 = 'a2222222-2222-4222-8222-222222222222'
export const REV3 = 'a3333333-3333-4333-8333-333333333333'
export const REVISE_JOB = 'b1111111-1111-4111-8111-111111111111'
export const MOWL_CONNECTION = 'c1111111-1111-4111-8111-111111111111'

export const CLASS_PATH = `/api/classes/${CLASS_ID}`

/** A `ClassOut` answer. */
export function classBody(extra: Record<string, unknown> = {}) {
  return {
    id: CLASS_ID,
    title: 'Tuesday climb',
    created_at: '2026-10-29T10:00:00Z',
    updated_at: '2026-10-29T11:00:00Z',
    current_revision_id: REV1,
    current_revision_number: 1,
    revision_count: 1,
    saved: { count: 0, latest_revision_number: null, latest_saved_at: null, latest_up_to_date: null },
    profile: [[2, 300], [5, 90], [3, 600]],
    open_job_id: null,
    open_job: null,
    ...extra,
  }
}

export function summaryBody(
  id: string,
  number: number,
  extra: Record<string, unknown> = {},
) {
  return {
    id,
    number,
    parent_id: number === 1 ? null : REV1,
    parent_number: number === 1 ? null : 1,
    origin: number === 1 ? 'design_draft' : 'revise_draft',
    created_at: '2026-10-29T10:05:00Z',
    is_current: number === 1,
    summary: { segments: 2, intervals: 4, total_sec: 1200 },
    cost_micros: 0,
    ...extra,
  }
}

export const CONTENT = {
  name: 'Tuesday climb <b>bold</b>',
  description: 'Two hills <img src=x onerror=alert(1)>',
  segments: [
    {
      name: 'Warm up',
      kind: 'warmup',
      intervals: [{ sec: 300, zone: 2, rpm: [80, 90], standing: false, cue: 'Easy now' }],
    },
    {
      name: 'Climb',
      kind: 'main',
      intervals: [
        { sec: 600, zone: 5, rpm: [60, 70], standing: true, cue: '<script>alert(1)</script>' },
        { sec: 300, zone: 1, rpm: 'beat', standing: false },
      ],
    },
    {
      name: 'Cool down',
      kind: 'cooldown',
      intervals: [{ sec: 120, zone: 1, rpm: [70, 80], standing: false, cue: '' }],
    },
  ],
}

export function revisionBody(
  id: string,
  number: number,
  content: Record<string, unknown> = CONTENT,
  extra: Record<string, unknown> = {},
) {
  return {
    ...summaryBody(id, number),
    kind: 'draft',
    content,
    unknown_bpm: [{ index: 3, title: 'Song <i>without</i> BPM' }],
    provenance: { origin: number === 1 ? 'design_draft' : 'revise_draft' },
    ...extra,
  }
}

export function message(
  seq: number,
  role: 'user' | 'assistant',
  body: string,
  extra: Record<string, unknown> = {},
) {
  return {
    seq,
    role,
    body,
    job_id: REVISE_JOB,
    base_revision_id: REV1,
    result_revision_id: null,
    change: null,
    created_at: '2026-10-29T10:30:00Z',
    ...extra,
  }
}

/** The routes of a class with one revision. */
export function classRoutes(
  overrides: {
    cls?: Record<string, unknown>
    revisions?: unknown[]
    messages?: unknown[]
    revisionBodies?: Record<string, unknown>
  } = {},
) {
  const revisions = overrides.revisions ?? [summaryBody(REV1, 1)]
  const routes: Record<string, object> = {
    [`GET ${CLASS_PATH}`]: classBody(overrides.cls),
    [`GET ${CLASS_PATH}/revisions`]: { revisions },
    [`GET ${CLASS_PATH}/messages`]: { messages: overrides.messages ?? [] },
    [`GET ${CLASS_PATH}/revisions/${REV1}`]: revisionBody(REV1, 1),
  }
  for (const [id, body] of Object.entries(overrides.revisionBodies ?? {})) {
    routes[`GET ${CLASS_PATH}/revisions/${id}`] = body as object
  }
  return routes
}
