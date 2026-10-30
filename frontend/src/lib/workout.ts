import type { DraftSegment } from '../api/jobs'

// Numbers shown for a draft, computed from the stored content (never from model text). TSS uses
// the weights of the `mowl` library (`workout.TSS_WEIGHT`) and its formula; the backend's own
// figures (the change summaries) come from the same library.

const TSS_WEIGHT: Record<number, number> = {
  1: 0.249,
  2: 0.42,
  3: 0.75,
  4: 1.039,
  5: 1.197,
  6: 1.937,
  7: 2.282,
}

export function segmentSeconds(segment: DraftSegment): number {
  return segment.intervals.reduce((sum, i) => sum + i.sec, 0)
}

export function totalSeconds(segments: DraftSegment[]): number {
  return segments.reduce((sum, s) => sum + segmentSeconds(s), 0)
}

export function tss(segments: DraftSegment[]): number {
  let sum = 0
  for (const segment of segments) {
    for (const i of segment.intervals) sum += i.sec * (TSS_WEIGHT[i.zone] ?? 0)
  }
  return Math.round((sum / 36) * 10) / 10
}

/** `m:ss` (minutes keep counting past an hour). */
export function clock(seconds: number): string {
  const minutes = Math.floor(seconds / 60)
  return `${String(minutes)}:${String(Math.round(seconds % 60)).padStart(2, '0')}`
}
