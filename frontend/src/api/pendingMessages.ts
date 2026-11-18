import { get, post } from './client'
import { list, object, optionalText, text } from './decode'

// Pending chat messages (ADR 0038 section 8; backend `classes/pending_routes.py`, #1186). A
// message sent while a job runs is stored as `pending`; the page submits it by id, and the server
// decides by the state of the class what it becomes. A refused submit leaves it `failed` with the
// reason code, never pending. Only while `composer_flow` is on (409 `feature_off` otherwise).

export type PendingStatus = 'pending' | 'submitted' | 'failed'

export interface PendingMessage {
  messageId: string
  body: string
  status: PendingStatus
  /** The closed refusal code of a `failed` message (`revision_limit`, `quota`, `not_changeable`, ...). */
  reason: string | null
  /** The job a `submitted` message became. */
  jobId: string | null
  createdAt: string
}

/** The server keeps at most this many stored messages per class (409 `pending_full`). */
export const MAX_PENDING_STORED = 50

const STATUSES: readonly string[] = ['pending', 'submitted', 'failed']

const base = (classId: string) => `/api/classes/${encodeURIComponent(classId)}/pending-messages`

function decodeMessage(value: unknown): PendingMessage {
  const item = object(value, 'pending message')
  const status = text(item, 'status')
  return {
    messageId: text(item, 'message_id'),
    body: text(item, 'body'),
    status: STATUSES.includes(status) ? (status as PendingStatus) : 'failed',
    reason: optionalText(item, 'reason'),
    jobId: optionalText(item, 'job_id'),
    createdAt: text(item, 'created_at'),
  }
}

/** The class's stored messages, oldest first. */
export async function listPending(classId: string, signal?: AbortSignal): Promise<PendingMessage[]> {
  const body = object(await get(base(classId), signal), 'pending messages')
  return list(body, 'messages').map(decodeMessage)
}

/** Stores a message as `pending`; the same id and body answers the same message (idempotent). */
export async function storePending(classId: string, messageId: string, body: string): Promise<PendingMessage> {
  return decodeMessage(await post(base(classId), { message_id: messageId, body }))
}

/**
 * Submits the message by its id. The answer is the message as it is (`submitted` with its job, or
 * `failed`); a 409 `class_busy` leaves it pending (the page tries again after the job), any other
 * 409 means it was refused and is now `failed` with that reason: read the list again.
 */
export async function submitPending(classId: string, messageId: string): Promise<PendingMessage> {
  return decodeMessage(await post(`${base(classId)}/${encodeURIComponent(messageId)}/submit`))
}
