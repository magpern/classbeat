import { CLASS_PATH, REV1, REV2, classRoutes, revisionBody } from './classFixtures'
import { json } from './fakeApi'
import { SESSION_ROUTES } from './jobFixtures'

// The wire side of the editor page's tests: the overlay and timeline answers of a studio draft
// whose class (see classFixtures `CONTENT`) has four blocks: warm up 0 to 300 s, climb 300 to 900 s,
// recovery 900 to 1200 s, cool down 1200 to 1320 s.

export const EDITS_PATH = `${CLASS_PATH}/edits`
export const CURRENT_PATH = `${CLASS_PATH}/current`
export const EDIT_PAGE = `${CLASS_PATH.replace('/api', '')}/edit`
export const HASH1 = '11'.repeat(32)
export const HASH2 = '22'.repeat(32)

export function overlayBody(revisionId: string, extra: Record<string, unknown> = {}) {
  return { revision_id: revisionId, records: [], unapplied: [], orphaned: [], undo: null, redo: null, ...extra }
}

export function timelineBody(revisionId: string) {
  return {
    revision_id: revisionId,
    content_hash: HASH1,
    duration_sec: 1320,
    workout_sec: 1320,
    is_plain: false,
    spec_digest: 'ab'.repeat(32),
    proposal_revision_id: 'f1111111-1111-4111-8111-111111111111',
    tracks: [
      {
        position: 0, title: 'First song', artist: 'Band', bpm: 120,
        start_sec: 10, end_sec: 700, played_sec: 690, effective_sec: 682,
        mix_start_sec: 0, mix_end_sec: 682, crossfade_in_sec: 0, crossfade_out_sec: 8,
        first_downbeat_sec: 0.5,
      },
      {
        position: 1, title: 'Second song', artist: 'Duo', bpm: 120,
        start_sec: 0, end_sec: 646, played_sec: 646, effective_sec: 638,
        mix_start_sec: 674, mix_end_sec: 1320, crossfade_in_sec: 8, crossfade_out_sec: 0,
        first_downbeat_sec: 0.5,
      },
    ],
    crossfades: [{ after: 0, sec: 8, mix_from_sec: 674, mix_to_sec: 682 }],
    intervals: [],
  }
}

/** The routes of a studio draft the editor can open; `extra` replaces any of them. */
export function editorRoutes(extra: Record<string, unknown> = {}) {
  return {
    ...SESSION_ROUTES,
    ...classRoutes({ cls: { studio: true }, revisionBodies: { [REV2]: revisionBody(REV2, 2) } }),
    [`GET ${CLASS_PATH}/overlay`]: overlayBody(REV1),
    [`GET ${CLASS_PATH}/timeline`]: timelineBody(REV1),
    // Not a Spotify draft: the listening preview has nothing to show (a 409), so no panel opens.
    [`GET ${CLASS_PATH}/preview/spotify/alignment`]: () => json({ detail: { reason: 'no_draft' } }, 409),
    ...extra,
  }
}

export const staleAnswer = () => json({ detail: { reason: 'stale_revision', current_revision_id: REV2 } }, 409)
export const refusal = (why: string) => json({ detail: { reason: why } }, 409)

export const PREVIEW_SESSION = 'c1111111-1111-4111-8111-111111111111'
export const PREVIEW_PATH = `${CLASS_PATH}/preview`
/** The clip request of a window that starts at `startMs`. */
export const clipPath = (startMs: number, session = PREVIEW_SESSION) => `GET ${PREVIEW_PATH}/clip?session=${session}&start_ms=${String(startMs)}`

/** The server's answer for a ready window (the browser's `Range: bytes=0-0` probe): one byte and the window's bounds. */
export const readyClip = (startMs: number, endMs: number) =>
  new Response('x', { status: 206, headers: { 'X-Preview-Start-Ms': String(startMs), 'X-Preview-End-Ms': String(endMs), 'Content-Type': 'audio/mpeg' } })

/** The routes of a working audio preview: a session and the first window (0 to 240 s). */
export function previewRoutes(extra: Record<string, unknown> = {}) {
  return {
    [`POST ${PREVIEW_PATH}/sessions`]: { session_id: PREVIEW_SESSION },
    [clipPath(0)]: () => readyClip(0, 240_000),
    ...extra,
  }
}
