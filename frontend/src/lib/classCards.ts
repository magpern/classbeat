import type { ClassSummary } from '../api/classes'
import { t } from '../i18n/t'

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

/** Why a TSS with a zone 7 interval is approximate (ADR 0012 section 7). */
export const tssApproximateNote = (): string => t('class.tss.approximate')

export function lengthLabel(seconds: number): string {
  const minutes = Math.round(seconds / 60)
  return minutes >= 60
    ? t('class.length.hoursMinutes', { hours: Math.floor(minutes / 60), minutes: minutes % 60 })
    : t('class.length.minutes', { minutes })
}

export type StatusTone = 'neutral' | 'ok' | 'warn'

export function classStatus(c: ClassSummary): { label: string; tone: StatusTone } {
  if (c.openJob?.kind === 'interpret_request') return { label: t('class.status.reading'), tone: 'warn' }
  if (c.openJob?.kind === 'design_draft') return { label: t('class.status.designing'), tone: 'warn' }
  if (c.openJob?.kind === 'revise_draft') return { label: t('class.status.changing'), tone: 'warn' }
  if (c.openJob !== null) return { label: t('class.status.busy'), tone: 'warn' }
  if (c.currentRevisionNumber === null) return { label: t('class.status.noVersion'), tone: 'neutral' }
  const number = c.currentRevisionNumber
  if (c.saved.count > 0) {
    return c.saved.latestUpToDate === false
      ? { label: t('class.status.unsavedChanges', { number }), tone: 'warn' }
      : { label: t('class.status.saved', { number }), tone: 'ok' }
  }
  return { label: t('class.status.notSaved', { number }), tone: 'neutral' }
}

export type ClassFilter = 'all' | 'saved' | 'drafts'

export function matchesFilter(c: ClassSummary, filter: ClassFilter): boolean {
  if (filter === 'saved') return c.saved.count > 0
  if (filter === 'drafts') return c.saved.count === 0
  return true
}
