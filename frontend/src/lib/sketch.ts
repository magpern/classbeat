// A rough sketch of the class for the "Your class so far" card (ADR 0013 section 5). It is a
// picture of the form's numbers only: it is never sent anywhere and the real design is Claude's.

import type { Brief } from '../api/jobs'

export type Effort = Brief['intensity']

/** The zones an effort level mostly works in, shown on the effort cards. */
export const EFFORTS: readonly { value: Effort; label: string; low: number; high: number; base: number }[] = [
  { value: 'easy', label: 'Easy', low: 1, high: 3, base: 2 },
  { value: 'moderate', label: 'Moderate', low: 2, high: 4, base: 3 },
  { value: 'hard', label: 'Hard', low: 3, high: 5, base: 4 },
]

export interface SketchInput {
  length: number
  effort: Effort
  climbs: number
  sprints: number
  races: number
}

/** (zone, seconds) pairs that add up to the class length; empty when the numbers are not usable. */
export function sketchProfile(input: SketchInput): [number, number][] {
  const { length, effort, climbs, sprints, races } = input
  if (![length, climbs, sprints, races].every(Number.isInteger) || length < 10 || length > 180) return []
  const base = EFFORTS.find((e) => e.value === effort)?.base ?? 3
  const parts: [number, number][] = [[1, 480]]
  const efforts: [number, number][] = []
  for (let i = 0; i < Math.min(Math.max(climbs, 0), 10); i += 1) efforts.push([Math.min(base + 2, 5), 240])
  for (let i = 0; i < Math.min(Math.max(sprints, 0), 20); i += 1) efforts.push([6, 30])
  for (let i = 0; i < Math.min(Math.max(races, 0), 5); i += 1) efforts.push([5, 300])
  for (const item of efforts) {
    parts.push([base, 120], item, [1, 60])
  }
  parts.push([base, 120], [1, 300])
  const total = parts.reduce((sum, [, sec]) => sum + sec, 0)
  const scale = (length * 60) / total
  return parts.map(([zone, sec]) => [zone, Math.max(1, Math.round(sec * scale))])
}
