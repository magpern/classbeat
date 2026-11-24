import { createInterpretJob } from '../../api/interpret'
import { composerInput, keptFromRequest, type ComposerRequest } from '../start/composerModel'
import { rememberRequest } from '../../lib/startRequest'
import { newIdempotencyKey } from '../../lib/submittedInput'

/**
 * "Continue without music": the same request again as a class with no music, through the start screen's own
 * "No music" route (an `interpret_request` job with `music_override: none`; ADR 0007's checks apply on the
 * server). Returns the new class's id; this class stays as it is.
 */
export async function startWithoutMusic(words: string, mowlConnectionId: string): Promise<string> {
  const sent: ComposerRequest = { words, mowlConnectionId, spotifyConnectionId: undefined, tags: { kind: 'none' }, sport: 'cycling' }
  const job = await createInterpretJob(composerInput(sent), newIdempotencyKey())
  rememberRequest(job.classId, keptFromRequest(sent))
  return job.classId
}
