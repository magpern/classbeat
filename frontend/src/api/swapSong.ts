import { ApiError, post } from './client'
import { object, text } from './decode'
import { actionKey, dropActionKey } from '../lib/submittedInput'

// Swap one song of the waiting list (ADR 0038 section 6, #1188): `POST /api/classes/{id}/swap-songs`
// with `{ song_list_revision_id, replace: [position] }` and an `Idempotency-Key`. The server takes
// the brief, the attached playlists and the connections from the waiting job itself, and it is NOT
// preceded by a cancel: the create transaction cancels the waiting job and rolls back when it is
// refused (409 `revision_limit`, `quota`, `pool_busy`, `not_waiting`, `expired`, ...), so the
// waiting list stays approvable after a refusal.

export async function swapSong(args: {
  classId: string
  listRevisionId: string
  /** The job that made the list: with the slot it names the action, so a retry reuses its key. */
  listJobId: string
  /** The song's slot, counted from 0: the list shows positions from 1, the API takes slots (ADR 0008 section 6). */
  slot: number
}): Promise<string> {
  const scope = `swap.${args.listJobId}.${String(args.slot)}`
  const path = `/api/classes/${encodeURIComponent(args.classId)}/swap-songs`
  try {
    const body = { song_list_revision_id: args.listRevisionId, replace: [args.slot] }
    const answer = object(await post(path, body, { 'Idempotency-Key': actionKey(scope) }), 'swap job')
    dropActionKey(scope)
    return text(answer, 'job_id')
  } catch (error) {
    if (error instanceof ApiError && error.status !== 409) dropActionKey(scope)
    throw error
  }
}
