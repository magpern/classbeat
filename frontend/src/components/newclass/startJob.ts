import { ApiError } from '../../api/client'
import { createDesignJob, type DesignInput } from '../../api/jobs'
import { createPickJob } from '../../api/songs'
import { rememberInput, rememberPick } from '../../lib/submittedInput'
import type { JobPlan } from './jobPlan'

/** One idempotency key per request a form submit may send: the song job and the design job. */
export interface AttemptKeys {
  songs: string
  draft: string
}

/**
 * Starts the job `planJob` chose and returns its id. A `source` job the server refuses with
 * `song_picking_off` (the kill switch of classbeat's Spotify writes, which writes nothing) becomes
 * the write-free `design_draft` on the same playlist; `onDraft` is called before that request so
 * the caller words its errors as a design's. Every other refusal is answered as it is.
 */
export async function startPlanned(plan: JobPlan, keys: AttemptKeys, onDraft: () => void): Promise<string> {
  if (plan.kind === 'draft') return draft(plan.draft, keys)
  try {
    const job = await createPickJob(plan.pick, keys.songs)
    rememberPick(job.id, plan.pick)
    return job.id
  } catch (error) {
    if (plan.kind !== 'source' || !(error instanceof ApiError) || error.reason !== 'song_picking_off') throw error
    onDraft()
    return draft(plan.fallback, keys)
  }
}

async function draft(input: DesignInput, keys: AttemptKeys): Promise<string> {
  const job = await createDesignJob(input, keys.draft)
  rememberInput(job.id, input)
  return job.id
}
