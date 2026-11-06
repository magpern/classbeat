import type { DraftInterval, DraftSegment } from '../api/jobs'
import type { CatalogueKey } from '../i18n/en'
import { t } from '../i18n/t'

// The numbers behind the class profile (ADR 0013 section 7), worked out from the stored segments.

/** The catalogue keys of MOWL's seven zone names (ADR 0013 section 2, `mowl.workout.ZONE_NAMES`). */
const ZONE_NAME_KEYS: readonly CatalogueKey[] = [
  'class.zoneName.recovery',
  'class.zoneName.endurance',
  'class.zoneName.tempo',
  'class.zoneName.threshold',
  'class.zoneName.vo2max',
  'class.zoneName.anaerobic',
  'class.zoneName.neuromuscular',
]

/** The seven zone names in the active language, looked up when called so a change of language shows at once. */
export function zoneNames(): string[] {
  return ZONE_NAME_KEYS.map((key) => t(key))
}

export function clampZone(zone: number): number {
  return Math.min(Math.max(Math.round(zone), 1), 7)
}

export function zoneName(zone: number): string {
  const key = ZONE_NAME_KEYS[clampZone(zone) - 1]
  return key === undefined ? '' : t(key)
}

export interface Bar extends DraftInterval {
  /** Seconds from the start of the class. */
  start: number
  /** The segment (part of the class) the interval belongs to. */
  part: string
  zone: number
}

/** Every interval in order, with its start time and the part it belongs to. */
export function flatten(segments: DraftSegment[]): Bar[] {
  const bars: Bar[] = []
  let at = 0
  for (const segment of segments) {
    for (const interval of segment.intervals) {
      bars.push({ ...interval, zone: clampZone(interval.zone), start: at, part: segment.name })
      at += interval.sec
    }
  }
  return bars
}

/** Bar height as a share of the plot: about 6 % plus 10.5 % per zone, leaving room for a marker. */
export function heightShare(zone: number): number {
  return 0.06 + 0.105 * clampZone(zone)
}

/** Seconds in each zone 1 to 7 (index 0 is zone 1). */
export function zoneSeconds(bars: Bar[]): number[] {
  const out = [0, 0, 0, 0, 0, 0, 0]
  for (const bar of bars) out[bar.zone - 1] = (out[bar.zone - 1] ?? 0) + bar.sec
  return out
}

export function cadenceText(rpm: [number, number] | 'beat' | null): string {
  if (rpm === null) return ''
  return rpm === 'beat' ? t('class.cadence.beat') : t('class.cadence.range', { low: rpm[0], high: rpm[1] })
}

/** A running interval's treadmill incline (ADR 0018 section 2): 0 is flat, 1 the raised incline. */
export function inclineText(gradient: number | null | undefined): string {
  return t(gradient === 1 ? 'class.incline.raised' : 'class.incline.flat')
}

/** How many zones a sport has: MOWL's running classes use 1 to 5, cycling 1 to 7. */
export function zoneCount(running: boolean): number {
  return running ? 5 : 7
}
