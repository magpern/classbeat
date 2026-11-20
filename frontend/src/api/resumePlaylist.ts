import { ApiError, post } from './client'
import { object, text } from './decode'
import { actionKey, dropActionKey } from '../lib/submittedInput'

/**
 * "Create the playlist again" (ADR 0038 section 7, #1189): `POST /api/classes/{id}/resume-songs`
 * with an `Idempotency-Key` and no body. The server builds the resume from the approval receipt
 * (the approved list, its hash, its sketch and `save`) and takes no credit; it is NOT
 * `POST /api/jobs` mode `resume`, which is `receipt_invalid` for a composer list. The key is kept
 * until the call succeeds, so a repeated click returns the same job.
 */
export async function resumePlaylist(args: { classId: string; listJobId: string }): Promise<string> {
  const scope = `resume.${args.listJobId}`
  try {
    const answer = object(await post(`/api/classes/${encodeURIComponent(args.classId)}/resume-songs`, {}, { 'Idempotency-Key': actionKey(scope) }), 'resume job')
    dropActionKey(scope)
    return text(answer, 'job_id')
  } catch (error) {
    if (error instanceof ApiError && error.status !== 409) dropActionKey(scope)
    throw error
  }
}
