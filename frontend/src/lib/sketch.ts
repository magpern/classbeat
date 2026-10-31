// A rough sketch of the class for the "Your class so far" card (ADR 0013 section 5). It is a
// picture of the form's numbers only: it is never sent anywhere and the real design is Claude's.
//
// The sketch is qualitative. A bar's `level` is a relative height from 1 (lowest) to 5 (highest)
// and means no effort zone: nothing in the backend defines which zones Easy, Moderate or Hard
// use, so the page shows no zone number, zone name or zone colour for them.

import type { Brief } from '../api/jobs'

export type Effort = Brief['intensity']

export const EFFORTS: readonly { value: Effort; label: string; steady: number }[] = [
  { value: 'easy', label: 'Easy', steady: 2 },
  { value: 'moderate', label: 'Moderate', steady: 3 },
  { value: 'hard', label: 'Hard', steady: 4 },
]

export const MAX_LEVEL = 5

export interface SketchInput {
  length: number
  effort: Effort
  climbs: number
  sprints: number
  races: number
}

/** (relative height, seconds) pairs that add up to the class length; empty when the numbers are not usable. */
export function sketchProfile(input: SketchInput): [number, number][] {
  const { length, effort, climbs, sprints, races } = input
  if (![length, climbs, sprints, races].every(Number.isInteger) || length < 10 || length > 180) return []
  const steady = EFFORTS.find((e) => e.value === effort)?.steady ?? 3
  const parts: [number, number][] = [[1, 480]]
  const efforts: [number, number][] = []
  for (let i = 0; i < Math.min(Math.max(climbs, 0), 10); i += 1) efforts.push([Math.min(steady + 1, MAX_LEVEL), 240])
  for (let i = 0; i < Math.min(Math.max(sprints, 0), 20); i += 1) efforts.push([MAX_LEVEL, 30])
  for (let i = 0; i < Math.min(Math.max(races, 0), 5); i += 1) efforts.push([MAX_LEVEL, 300])
  for (const item of efforts) {
    parts.push([steady, 120], item, [1, 60])
  }
  parts.push([steady, 120], [1, 300])
  const total = parts.reduce((sum, [, sec]) => sum + sec, 0)
  const scale = (length * 60) / total
  return parts.map(([level, sec]) => [level, Math.max(1, Math.round(sec * scale))])
}
