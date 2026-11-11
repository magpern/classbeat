import type { TFunction } from '../../i18n/t'
import type { Brief, ExplicitPath, RunningBrief } from '../../api/jobs'
import { briefBytes, MAX_BRIEF_BYTES } from '../../lib/brief'
import type { Music } from '../songs/MusicChoice'

// The rules of the New class form (pure: no network, no React). The page keeps the state and the
// request; this file decides what is valid and what the design brief is.

export const LENGTHS = [30, 45, 60, 90] as const

export function whole(value: string): number {
  return value.trim() === '' ? Number.NaN : Number(value)
}

function inRange(value: number, min: number, max: number): boolean {
  return Number.isInteger(value) && value >= min && value <= max
}

export function lengthSentence(t: TFunction, max: number): string {
  return t('newclass.planMaxLength', { count: max })
}

/** The parts of a class the plan leaves out, as one sentence, or null when it leaves out none. */
export function leftOut(t: TFunction, names: string[]): string | null {
  if (names.length === 0) return null
  const list =
    names.length === 1
      ? names[0]!
      : t('newclass.list.and', { head: names.slice(0, -1).join(', '), last: names[names.length - 1]! })
  return t('newclass.planLeavesOut', { list })
}

export interface FormValues {
  title: string
  playlist: string
  mixId: string
  music: Music
  running: boolean
  noMusic: boolean
  length: string
  intensity: Brief['intensity']
  climbs: string
  sprints: string
  races: string
  ftp: string
  style: string
  notes: string
  racesOn: boolean
  ftpOn: boolean
  /** The plan's longest class in minutes; null while unknown (the server still enforces it). */
  maxMinutes: number | null
}

/**
 * Every value the form submits is one the instructor chose, so it counts as stated (ADR 0029 section
 * 4): the draft is held to it and a miss is reported under Not as asked. A count of 0 is "none
 * asked", not a stated value, and `ftp_test` is stated only when a test is set; a running class has
 * no bike knobs. Intensity is left out: nothing checks it against the draft.
 */
export function explicitPaths(running: boolean, counts: Pick<Brief, 'climbs' | 'sprints' | 'races' | 'ftp_test'>): ExplicitPath[] {
  if (running) return ['length_min']
  const paths: ExplicitPath[] = ['length_min']
  for (const path of ['climbs', 'sprints', 'races'] as const) if (counts[path] > 0) paths.push(path)
  if (counts.ftp_test !== null) paths.push('ftp_test')
  return paths
}

/** The first problem with the form as a sentence, or the brief to design from. */
export function validateForm(t: TFunction, v: FormValues): { problem: string } | { brief: Brief | RunningBrief } {
  const { running, noMusic } = v
  const parts = {
    length_min: whole(v.length),
    climbs: running ? 0 : whole(v.climbs),
    sprints: running ? 0 : whole(v.sprints),
    races: running || !v.racesOn ? 0 : whole(v.races),
  }
  const name = v.title.trim()
  let problem: string | null = null
  if (name === '' || name.length > 100) problem = t('newclass.invalid.name')
  else if (!noMusic && (running || v.music === 'playlist') && v.playlist === '') problem = t('newclass.invalid.playlist')
  else if (!running && !noMusic && v.music === 'swedebeat_mix' && v.mixId === '') problem = t('newclass.invalid.mix')
  else if (!inRange(parts.length_min, 10, 180)) problem = t('newclass.invalid.length')
  else if (v.maxMinutes !== null && parts.length_min > v.maxMinutes) problem = lengthSentence(t, v.maxMinutes)
  else if (!inRange(parts.climbs, 0, 10)) problem = t('newclass.invalid.climbs')
  else if (!inRange(parts.sprints, 0, 20)) problem = t('newclass.invalid.sprints')
  else if (!inRange(parts.races, 0, 5)) problem = t('newclass.invalid.races')
  else if (v.style.length > 60) problem = t('newclass.invalid.style')
  else if (v.notes.length > 2000) problem = t('newclass.invalid.notes')
  if (problem !== null) return { problem }
  // A running brief names its sport and has none of the bike-only knobs (the backend refuses them).
  const brief: Brief | RunningBrief = running
    ? {
        sport: 'running',
        length_min: parts.length_min,
        intensity: v.intensity,
        style: v.style,
        text: v.notes,
        explicit: explicitPaths(true, { climbs: 0, sprints: 0, races: 0, ftp_test: null }),
      }
    : (() => {
        const ftp_test = v.ftp === '' || !v.ftpOn ? null : (Number(v.ftp) as 3 | 5 | 20)
        return {
          ...parts,
          intensity: v.intensity,
          ftp_test,
          style: v.style,
          text: v.notes,
          explicit: explicitPaths(false, { ...parts, ftp_test }),
        }
      })()
  // The limit is in bytes (the brief is stored as jsonb), not characters.
  if (briefBytes(brief) > MAX_BRIEF_BYTES) return { problem: t('newclass.invalid.briefTooLong') }
  return { brief }
}
