// The gentle nudge toward Individual (M7a-4): shown at most once for each trigger and never again
// in the session after a dismissal. Kept in memory and, when the browser allows it, in
// sessionStorage so a reload inside the tab does not bring it back either.

const KEY = 'classbeat.nudge.dismissed'
let dismissedNow = false
const claims = new Map<string, string>()

export function nudgeDismissed(): boolean {
  if (dismissedNow) return true
  try {
    return sessionStorage.getItem(KEY) === '1'
  } catch {
    return false
  }
}

export function dismissNudge(): void {
  dismissedNow = true
  try {
    sessionStorage.setItem(KEY, '1')
  } catch {
    // Private window or blocked storage: the in-memory flag still holds for this page.
  }
}

/** A once-per-session trigger: the first owner to ask keeps it (asking again as the same owner is fine). */
export function claimNudge(trigger: string, owner: string): boolean {
  const held = claims.get(trigger)
  if (held === undefined) claims.set(trigger, owner)
  return held === undefined || held === owner
}

/** Test helper: a fresh session. */
export function resetNudge(): void {
  dismissedNow = false
  claims.clear()
  try {
    sessionStorage.removeItem(KEY)
  } catch {
    // Nothing stored.
  }
}
