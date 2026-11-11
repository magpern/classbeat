import { describe, expect, it } from 'vitest'
import { BAR_ZONES, HERO_BPM, barLevel, beatPhase, kick, markerAngle, zoneOfLevel } from './beatField'
import { REVEAL_MS, revealSettleMs, startsHidden } from './motionPolicy'

describe('beat maths', () => {
  it('has the phase at 0 on each beat and below 1 between', () => {
    expect(beatPhase(0, 120)).toBe(0)
    expect(beatPhase(0.5, 120)).toBeCloseTo(0, 10) // 120 BPM: a beat every half second
    expect(beatPhase(0.25, 120)).toBeCloseTo(0.5, 10)
    for (let s = 0; s < 10; s += 0.137) expect(beatPhase(s, HERO_BPM)).toBeLessThan(1)
  })

  it('kicks hardest on the beat and dies away', () => {
    expect(kick(0)).toBe(1)
    expect(kick(0.9)).toBeLessThan(0.05)
  })

  it('keeps every bar between 0 and 1, and the bars are higher on the beat than off it', () => {
    let onBeat = 0
    let offBeat = 0
    for (let i = 0; i < 96; i++) {
      for (const s of [0, 0.31, 1.7, 9.2]) {
        const level = barLevel(i, 96, s, HERO_BPM)
        expect(level).toBeGreaterThanOrEqual(0)
        expect(level).toBeLessThanOrEqual(1)
      }
      onBeat += barLevel(i, 96, 2, HERO_BPM)
      offBeat += barLevel(i, 96, 2.4, HERO_BPM)
    }
    expect(onBeat).toBeGreaterThan(offBeat)
  })

  it('has no seam: the last bar is as near the first as any two neighbours', () => {
    const gaps = Array.from({ length: 95 }, (_, i) => Math.abs(barLevel(i + 1, 96, 3, 0) - barLevel(i, 96, 3, 0)))
    const seam = Math.abs(barLevel(0, 96, 3, 0) - barLevel(95, 96, 3, 0))
    expect(seam).toBeLessThanOrEqual(Math.max(...gaps) + 1e-9)
  })

  it('draws low bars in zone 1 and full bars in the top zone, whatever the input', () => {
    expect(zoneOfLevel(0)).toBe(1)
    expect(zoneOfLevel(1)).toBe(BAR_ZONES)
    expect(zoneOfLevel(-3)).toBe(1)
    expect(zoneOfLevel(7)).toBe(BAR_ZONES)
  })

  it('turns the marker half a turn every four beats', () => {
    expect(markerAngle(4 * (60 / 120), 120)).toBeCloseTo(Math.PI, 10)
  })
})

describe('motion policy', () => {
  it('hides only what lies fully below the first screen', () => {
    expect(startsHidden(900, 800)).toBe(true)
    expect(startsHidden(800, 800)).toBe(true)
    expect(startsHidden(120, 800)).toBe(false)
    expect(startsHidden(-400, 800)).toBe(false) // scrolled past, e.g. a reload that kept the position
  })

  it('waits out the transition and the delay before dropping the attribute', () => {
    expect(revealSettleMs(0)).toBeGreaterThan(REVEAL_MS)
    expect(revealSettleMs(240)).toBe(revealSettleMs(0) + 240)
  })
})
