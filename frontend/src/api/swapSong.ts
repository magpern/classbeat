import { ApiError } from './client'
import type { Brief } from './jobs'
import { createPickJob, type PickInput } from './songs'
import { classContext, currentConnectionIds, followUpBrief } from '../lib/songs'
import { actionKey, dropActionKey } from '../lib/submittedInput'

// Swap one song of the waiting list (ADR 0038 section 6, backend #1188): replace mode, as
// `revise_songs` does today. It is NOT preceded by a cancel: the create transaction cancels the
// waiting job itself and rolls back when it is refused, so the waiting list stays approvable after
// a refusal (`revision_limit`, `quota`, `pool_busy`).
//
// Assumed input (not fixed by the ADR): the `design_with_songs` input of `pick` with `mode: "swap"`,
// the sketch as `base_revision_id`, and the list and position as `seed`.

export interface SwapInput extends Omit<PickInput, 'mode'> {
  mode: 'swap'
  /** The sketch the fill started from (every `design_with_songs` job names it). */
  base_revision_id: string
  brief: Brief
}

export async function swapSong(args: {
  classId: string
  sketchRevisionId: string
  listRevisionId: string
  listJobId: string
  targetSec: number
  position: number
}): Promise<string> {
  const ids = await currentConnectionIds()
  const context = await classContext(args.classId)
  const input: SwapInput = {
    class_id: args.classId,
    mode: 'swap',
    base_revision_id: args.sketchRevisionId,
    brief: followUpBrief(context.brief, args.targetSec, ''),
    seed: { song_list_revision_id: args.listRevisionId, replace: [args.position] },
    mowl_connection_id: ids.mowl,
    spotify_connection_id: ids.spotify,
  }
  const scope = `swap.${args.listJobId}.${String(args.position)}`
  try {
    // `createPickJob` types its input as a pick; the swap is the same endpoint and shape.
    const job = await createPickJob(input as unknown as PickInput, actionKey(scope))
    dropActionKey(scope)
    return job.id
  } catch (error) {
    if (error instanceof ApiError && error.status !== 409) dropActionKey(scope)
    throw error
  }
}
