import { post } from './client'
import { decodeJob, type Job } from './jobs'

// Save and Update (ADR 0007 sections 5 to 7). The approval is the click on one revision: the
// browser names rows by id and hash, never a workout body, a program id or a playlist id.

export interface SaveInput {
  class_id: string
  revision_id: string
  /** Lower-case hex SHA-256 of the revision's canonical content, as the page shows it. */
  content_hash: string
  mowl_connection_id: string
}

export interface UpdateInput extends SaveInput {
  /** The user's own saved row of this class: the only program an Update may write. */
  saved_workout_id: string
  /** Set only by the page's "Update anyway" confirmation (section 7). */
  overwrite: boolean
}

export async function createSaveJob(input: SaveInput, idempotencyKey: string): Promise<Job> {
  const body = { kind: 'save_workout', input }
  return decodeJob(await post('/api/jobs', body, { 'Idempotency-Key': idempotencyKey }))
}

export async function createUpdateJob(input: UpdateInput, idempotencyKey: string): Promise<Job> {
  const body = { kind: 'update_workout', input }
  return decodeJob(await post('/api/jobs', body, { 'Idempotency-Key': idempotencyKey }))
}

export const SAVE_KIND = 'save_workout'
export const UPDATE_KIND = 'update_workout'
export const SAVE_OPERATION = 'mowl_save_workout'
export const UPDATE_OPERATION = 'mowl_update_workout'
