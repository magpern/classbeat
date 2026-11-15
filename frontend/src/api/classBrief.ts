import { flag, isObject, object, text } from './decode'
import type { Brief } from './jobs'

// How the class answer's brief, profile and Swedebeat pointer are read, split out of `classes.ts`.

const INTENSITIES: readonly unknown[] = ['easy', 'moderate', 'hard']

/** The class's brief; anything malformed reads as none, and callers then use their fallback. */
export function decodeBrief(value: unknown): Brief | null {
  if (!isObject(value)) return null
  const whole = (key: string): number | null => {
    const n = value[key]
    return typeof n === 'number' && Number.isInteger(n) ? n : null
  }
  const length = whole('length_min')
  const climbs = whole('climbs')
  const sprints = whole('sprints')
  const races = whole('races')
  const ftp = value.ftp_test
  if (length === null || climbs === null || sprints === null || races === null) return null
  if (!INTENSITIES.includes(value.intensity)) return null
  if (ftp !== null && ftp !== 3 && ftp !== 5 && ftp !== 20) return null
  if (typeof value.style !== 'string' || typeof value.text !== 'string') return null
  return {
    length_min: length,
    intensity: value.intensity as Brief['intensity'],
    climbs,
    sprints,
    races,
    ftp_test: ftp,
    style: value.style,
    text: value.text,
  }
}

/**
 * Absent means no profile. An interval that is not a pair of whole numbers (zone 0 to 99,
 * seconds 0 or more) is skipped, so one bad interval never fails the list or hides the rest.
 */
export function decodeProfile(value: unknown): [number, number][] {
  if (!Array.isArray(value)) return []
  const out: [number, number][] = []
  for (const entry of value as unknown[]) {
    if (!Array.isArray(entry) || entry.length !== 2) continue
    const [zone, sec] = entry as unknown[]
    if (!Number.isInteger(zone) || !Number.isInteger(sec)) continue
    if ((zone as number) < 0 || (zone as number) > 99 || (sec as number) < 0) continue
    out.push([zone as number, sec as number])
  }
  return out
}

export function decodeMatchPointer(value: unknown): { revisionId: string; approved: boolean } | null {
  if (value === null || value === undefined) return null
  const item = object(value, 'swedebeat_match')
  return { revisionId: text(item, 'revision_id'), approved: flag(item, 'approved') }
}
