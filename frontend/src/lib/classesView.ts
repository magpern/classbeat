import type { ClassSummary } from '../api/classes'

// What My classes shows from the list it already has (pure; no I/O).

/** A class with a version: it has a profile to show and a version to open or duplicate. */
export function hasVersion(c: ClassSummary): boolean {
  return c.currentRevisionNumber !== null
}

/** The newest class that has a version (by last change; the earlier one in the list on a tie). */
export function featureClass(classes: readonly ClassSummary[]): ClassSummary | null {
  let best: ClassSummary | null = null
  let bestTime = Number.NEGATIVE_INFINITY
  for (const c of classes) {
    if (!hasVersion(c)) continue
    const time = Date.parse(c.updatedAt)
    const at = Number.isNaN(time) ? Number.NEGATIVE_INFINITY : time
    if (best === null || at > bestTime) {
      best = c
      bestTime = at
    }
  }
  return best
}

export function classesStats(classes: readonly ClassSummary[]): { total: number; saved: number } {
  return { total: classes.length, saved: classes.filter((c) => c.saved.count > 0).length }
}
