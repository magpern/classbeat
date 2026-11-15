import { post } from './client'
import { decodeJob, type Job } from './jobs'

// Send to player (ADR 0034 section 5). The press is the approval of one revision: the browser
// sends ids and the revision's content hash, never a document or a publication number.

export interface SendToPlayerInput {
  class_id: string
  revision_id: string
  /** Lower-case hex SHA-256 of the revision's canonical content, as the page shows it. */
  content_hash: string
}

export const PLAYER_SEND_KIND = 'send_to_player'
/** The external-operation kinds of the player (backend `operations/kinds.py`). */
export const PLAYER_SEND_OPERATION = 'player_send'
export const PLAYER_REMOVE_OPERATION = 'player_remove'

export async function createSendToPlayerJob(
  input: SendToPlayerInput,
  idempotencyKey: string,
): Promise<Job> {
  const body = { kind: PLAYER_SEND_KIND, input }
  return decodeJob(await post('/api/jobs', body, { 'Idempotency-Key': idempotencyKey }))
}
