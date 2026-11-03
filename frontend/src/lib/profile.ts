import type { DraftInterval, DraftSegment } from '../api/jobs'

// The numbers behind the class profile (ADR 0013 section 7), worked out from the stored segments.

/** MOWL's seven zones and their names (ADR 0013 section 2, `mowl.workout.ZONE_NAMES`). */
export const ZONE_NAMES = [
  'Recovery',
  'Endurance',
  'Tempo',
  'Threshold',
  'VO2max',
  'Anaerobic',
  'Neuromuscular',
] as const

export function clampZone(zone: number): number {
  return Math.min(Math.max(Math.round(zone), 1), 7)
}

export function zoneName(zone: number): string {
  return ZONE_NAMES[clampZone(zone) - 1] ?? ''
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
  return rpm === 'beat' ? 'on the beat' : `${String(rpm[0])} to ${String(rpm[1])} rpm`
}

/** A running interval's treadmill incline (ADR 0018 section 2): 0 is flat, 1 the raised incline. */
export function inclineText(gradient: number | null | undefined): string {
  return gradient === 1 ? 'Incline' : 'Flat'
}

/** How many zones a sport has: MOWL's running classes use 1 to 5, cycling 1 to 7. */
export function zoneCount(running: boolean): number {
  return running ? 5 : 7
}
