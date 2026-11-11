// The maths of the hero's "flywheel": a ring of bars that kick on every beat of the example class.
// Pure functions of time, so they are testable and the same on every machine. Drawing is
// components/landing/drawBeatField.ts.

/** The tempo of the hero's pulse: the middle of the example class's songs (96 to 128 BPM). */
export const HERO_BPM = 120

/** Zones the bars climb through as they grow: 1 (recovery) to 5 (hard). Zones 5 to 7 share one red. */
export const BAR_ZONES = 5

/** Where we are inside the current beat: 0 at the kick, rising to just under 1 at the next. */
export function beatPhase(seconds: number, bpm: number): number {
  const beats = (seconds * bpm) / 60
  return beats - Math.floor(beats)
}

/** A sharp hit that dies away: 1 on the beat, about 0.05 two thirds of the way to the next. */
export function kick(phase: number): number {
  return Math.exp(-phase * 4.5)
}

const TAU = Math.PI * 2

/**
 * Height of bar `index` of `count` at time `seconds`, 0 to 1. A slow wave runs round the ring all
 * the time; on each beat every bar jumps, the most where a hot-spot (which drifts round) sits.
 * The wave uses whole numbers of periods round the ring, so the ring has no seam.
 */
export function barLevel(index: number, count: number, seconds: number, bpm: number): number {
  const angle = (index / count) * TAU
  const hit = kick(beatPhase(seconds, bpm))
  const wave = 0.5 + 0.25 * Math.sin(3 * angle + seconds * 1.3) + 0.25 * Math.sin(7 * angle - seconds * 0.8)
  const hotSpot = 0.5 + 0.5 * Math.cos(2 * angle - seconds * 0.4)
  const level = 0.12 + 0.38 * wave + 0.5 * hit * (0.35 + 0.65 * hotSpot)
  return Math.min(1, Math.max(0, level))
}

/** The effort zone a bar of this height is drawn in: low bars cool (1), full bars hot (5). */
export function zoneOfLevel(level: number): number {
  return 1 + Math.min(BAR_ZONES - 1, Math.floor(Math.min(1, Math.max(0, level)) * BAR_ZONES))
}

/** Angle of the orbiting marker, in radians: half a turn every four beats, like a pedal cadence. */
export function markerAngle(seconds: number, bpm: number): number {
  return ((seconds * bpm) / 60) * (Math.PI / 4)
}
