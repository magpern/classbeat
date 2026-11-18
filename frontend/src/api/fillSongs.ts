import { post } from './client'
import { object, text } from './decode'

// The fill hand-off (ADR 0038 section 5; backend #1188): `POST /api/classes/{id}/fill-songs`
// starts `design_with_songs` in mode `fill` for the class's current sketch. Idempotent: the key is
// a hash of the class id, the sketch's revision id and an `attempt` number the server stores on the
// class, so a reopened tab, or two tabs, get the same job. "Find songs again" sends `retry: true`
// (a failed, replaced or expired fill of the same sketch), which makes the next attempt.
//
// The body is `{ base_revision_id, retry? }` and the answer `{ job_id, kind, state }` (201 for a new
// job, 200 for the same attempt again). `base_revision_id` is the sketch the page shows; the server
// refuses another current revision with 409 `stale_base`. A refusal of the create is stored and
// answered again until a call with `retry`; the class answer's `fill_songs.failure` carries it.

export interface FillRequest {
  classId: string
  sketchRevisionId: string
  /** True for "Find songs again": the next attempt. */
  retry: boolean
}

export async function fillSongs(request: FillRequest): Promise<string> {
  const body = {
    base_revision_id: request.sketchRevisionId,
    ...(request.retry ? { retry: true } : {}),
  }
  const answer = object(await post(`/api/classes/${encodeURIComponent(request.classId)}/fill-songs`, body), 'fill job')
  return text(answer, 'job_id')
}
