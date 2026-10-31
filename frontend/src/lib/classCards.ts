import type { ClassSummary } from '../api/classes'

// What a class card shows, worked out from the class list (never from model text).

const TSS_WEIGHT: Record<number, number> = {
  1: 0.249,
  2: 0.42,
  3: 0.75,
  4: 1.039,
  5: 1.197,
  6: 1.937,
  7: 2.282,
}

export function profileSeconds(profile: [number, number][]): number {
  return profile.reduce((sum, [, sec]) => sum + sec, 0)
}

/** Same formula and weights as `lib/workout.ts`, from the list's (zone, seconds) pairs. */
export function profileTss(profile: [number, number][]): number {
  const sum = profile.reduce((total, [zone, sec]) => total + sec * (TSS_WEIGHT[zone] ?? 0), 0)
  return Math.round((sum / 36) * 10) / 10
}

export const TSS_APPROXIMATE_NOTE = 'approximate: zone 7 has no exact TSS weight'

export function lengthLabel(seconds: number): string {
  const minutes = Math.round(seconds / 60)
  return minutes >= 60 ? `${String(Math.floor(minutes / 60))} h ${String(minutes % 60)} min` : `${String(minutes)} min`
}

export type StatusTone = 'neutral' | 'ok' | 'warn'

export function classStatus(c: ClassSummary): { label: string; tone: StatusTone } {
  if (c.openJob?.kind === 'design_draft') return { label: 'Being designed', tone: 'warn' }
  if (c.openJob?.kind === 'revise_draft') return { label: 'Being changed', tone: 'warn' }
  if (c.openJob !== null) return { label: 'Busy', tone: 'warn' }
  if (c.currentRevisionNumber === null) return { label: 'No version yet', tone: 'neutral' }
  if (c.saved.count > 0) {
    return c.saved.latestUpToDate === false
      ? { label: `Version ${String(c.currentRevisionNumber)} has changes not in MOWL yet`, tone: 'warn' }
      : { label: `Version ${String(c.currentRevisionNumber)} saved to MOWL`, tone: 'ok' }
  }
  return { label: `Version ${String(c.currentRevisionNumber)}, not saved to MOWL`, tone: 'neutral' }
}

export type ClassFilter = 'all' | 'saved' | 'drafts'

export function matchesFilter(c: ClassSummary, filter: ClassFilter): boolean {
  if (filter === 'saved') return c.saved.count > 0
  if (filter === 'drafts') return c.saved.count === 0
  return true
}
