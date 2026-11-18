import { post } from './client'
import { decodeJob, type Job } from './jobs'
import { createSaveJob } from './saving'

// Approve and save (ADR 0038 section 7; backend #1189). The click on Approve carries `save: true`
// with the song list's revision id and content hash, exactly as ADR 0008 section 5's approval does.
// Phase 2 then stores the first draft and the class keeps `save_pending`; the page's driver saves
// exactly that revision and hash, naming the phase-2 job as `save_pending_job_id`.

/** `POST /api/jobs/{id}/approve` with `save: true`: one click approves both writes. */
export async function approveAndSave(jobId: string, revisionId: string, contentHash: string): Promise<Job> {
  return decodeJob(
    await post(`/api/jobs/${encodeURIComponent(jobId)}/approve`, {
      revision_id: revisionId,
      content_hash: contentHash,
      save: true,
    }),
  )
}

/** The idempotency key of the save of a phase-2 job: the same on every tab and every reload. */
export function savePendingKey(phase2JobId: string): string {
  return `save-pending-${phase2JobId}`.slice(0, 64)
}

export interface SavePendingTarget {
  classId: string
  revisionId: string
  contentHash: string
  /** The phase-2 job that stored the revision (`save_pending.job_id`). */
  jobId: string
  mowlConnectionId: string
}

/**
 * Saves exactly the revision and hash `save_pending` names, never "the current revision". The
 * save admit checks them against `save_pending` because `save_pending_job_id` is present.
 */
export function saveExactly(target: SavePendingTarget): Promise<Job> {
  return createSaveJob(
    {
      class_id: target.classId,
      revision_id: target.revisionId,
      content_hash: target.contentHash,
      mowl_connection_id: target.mowlConnectionId,
      save_pending_job_id: target.jobId,
    },
    savePendingKey(target.jobId),
  )
}
