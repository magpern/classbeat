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
