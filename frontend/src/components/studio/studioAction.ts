import type { StudioAction } from '../../api/studioSongs'

// The action that made a studio job (undo, another, swap anyway), kept per job in this tab's
// session storage: the job itself does not say, and a failed "another" must be explained (it
// leaves no list to approve, since the waiting one was cancelled). Storage can be blocked or
// full, so every access is guarded and the page then words the failure without the action.

const PREFIX = 'classbeat.studio-action.'
const ACTIONS: readonly string[] = ['undo', 'another', 'swap-anyway']

export function rememberStudioAction(jobId: string, action: StudioAction): void {
  try {
    window.sessionStorage.setItem(PREFIX + jobId, action)
  } catch {
    // Without storage the failure is worded without the action.
  }
}

export function recallStudioAction(jobId: string): StudioAction | null {
  try {
    const kept = window.sessionStorage.getItem(PREFIX + jobId)
    return kept !== null && ACTIONS.includes(kept) ? (kept as StudioAction) : null
  } catch {
    return null
  }
}

// The design job last started for a class, kept in this tab's session storage: the class page has
// no failed-job field, and a design that failed is worded as failed, not as never started.
const DESIGN = 'classbeat.studio-design.'

export function rememberStudioDesign(classId: string, jobId: string): void {
  try {
    window.sessionStorage.setItem(DESIGN + classId, jobId)
  } catch {
    // Without storage the page words it as not started.
  }
}

export function recallStudioDesign(classId: string): string | null {
  try {
    return window.sessionStorage.getItem(DESIGN + classId)
  } catch {
    return null
  }
}

// The day a class's output attempts hit the limit (`output_limit`, 24 per class and 24 per day),
// kept in this tab's session storage so the class page stops offering "approve again" the same
// day. Never stale for long: the day limit resets, and the class limit is found again by the next try.
const LIMIT = 'classbeat.studio-output-limit.'
const today = () => new Date().toISOString().slice(0, 10)

export function rememberOutputLimit(classId: string): void {
  try {
    window.sessionStorage.setItem(LIMIT + classId, today())
  } catch {
    // Without storage the page offers approval and the job says why it stopped.
  }
}

export function outputLimitReached(classId: string): boolean {
  try {
    return window.sessionStorage.getItem(LIMIT + classId) === today()
  } catch {
    return false
  }
}
