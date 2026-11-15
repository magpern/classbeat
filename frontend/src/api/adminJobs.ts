import { get } from './client'
import { count, flag, list, object, optionalText, text } from './decode'

// The admin Jobs view (ADR 0011 section 6, backend/src/classbeat/admin/jobs.py, #221), read
// only: jobs by state, waiting approvals, recent failures, uncertain external operations, and the
// player events that could not be delivered (ADR 0034 section 2, #1058). A failed player event
// carries no user and no subject: its kind, when it was written, when delivery gave up, the
// attempts and the last error category. Server text is data, rendered as text only.

export interface Jobs {
  days: number
  states: { state: string; count: number }[]
  waitingApprovals: {
    userId: string
    email: string | null
    kind: string
    waitingSince: string
    expiresAt: string | null
  }[]
  failures: { kind: string; category: string; count: number; lastAt: string }[]
  uncertainOperations: {
    userId: string
    email: string | null
    kind: string
    since: string
    jobState: string | null
  }[]
  failedPlayerEvents: {
    kind: string
    createdAt: string
    giveUpAt: string
    attempts: number
    lastError: string | null
  }[]
  /** More failed player events exist than the list shows. */
  playerEventsTruncated: boolean
}

export const JOB_WINDOWS = [7, 30, 90] as const

export function jobsPath(days: number): string {
  return `/api/admin/jobs?days=${String(days)}`
}

export async function getJobs(days: number, signal?: AbortSignal): Promise<Jobs> {
  const body = object(await get(jobsPath(days), signal), 'jobs')
  return {
    days: count(body, 'days'),
    states: list(body, 'states').map((row) => {
      const item = object(row, 'state')
      return { state: text(item, 'state'), count: count(item, 'count') }
    }),
    waitingApprovals: list(body, 'waiting_approvals').map((row) => {
      const item = object(row, 'waiting approval')
      return {
        userId: text(item, 'user_id'),
        email: optionalText(item, 'email'),
        kind: text(item, 'kind'),
        waitingSince: text(item, 'waiting_since'),
        expiresAt: optionalText(item, 'expires_at'),
      }
    }),
    failures: list(body, 'failures').map((row) => {
      const item = object(row, 'failure')
      return {
        kind: text(item, 'kind'),
        category: text(item, 'category'),
        count: count(item, 'count'),
        lastAt: text(item, 'last_at'),
      }
    }),
    uncertainOperations: list(body, 'uncertain_operations').map((row) => {
      const item = object(row, 'uncertain operation')
      return {
        userId: text(item, 'user_id'),
        email: optionalText(item, 'email'),
        kind: text(item, 'kind'),
        since: text(item, 'since'),
        jobState: optionalText(item, 'job_state'),
      }
    }),
    failedPlayerEvents: list(body, 'failed_player_events').map((row) => {
      const item = object(row, 'failed player event')
      return {
        kind: text(item, 'kind'),
        createdAt: text(item, 'created_at'),
        giveUpAt: text(item, 'give_up_at'),
        attempts: count(item, 'attempts'),
        lastError: optionalText(item, 'last_error'),
      }
    }),
    playerEventsTruncated: flag(body, 'player_events_truncated'),
  }
}
