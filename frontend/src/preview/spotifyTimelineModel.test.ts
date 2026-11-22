import { describe, expect, it } from 'vitest'
import { classMsAt, cursorPct, layoutOf, playTarget } from './spotifyTimelineModel'
import { buildSpotifyModel, type SpotifySong } from './timeModel'

const song = (piece: number, lengthMs: number): SpotifySong => ({
  piece,
  label: `Song ${String(piece + 1)}`,
  lengthMs,
  alignmentOffsetMs: 0,
  correctionOffsetMs: 0,
  timingClass: 'good',
})

// Three songs of 200 s with a 10 s crossfade: windows 0-200, 190-390, 380-580.
const model = buildSpotifyModel([song(0, 200_000), song(1, 200_000), song(2, 200_000)], 10_000)

describe('layoutOf', () => {
  it('places each song and alternates its row', () => {
    const { bars, totalMs } = layoutOf(model)
    expect(totalMs).toBe(580_000)
    expect(bars.map((b) => b.row)).toEqual([0, 1, 0])
    expect(bars[1]?.leftPct).toBeCloseTo((190 / 580) * 100)
    expect(bars[1]?.widthPct).toBeCloseTo((200 / 580) * 100)
  })

  it('has one overlap per crossfade, at the right place', () => {
    const { overlaps } = layoutOf(model)
    expect(overlaps.map((o) => [o.outgoing, o.incoming, o.startMs, o.endMs])).toEqual([
      [0, 1, 190_000, 200_000],
      [1, 2, 380_000, 390_000],
    ])
  })

  it('an empty class has an empty strip', () => {
    expect(layoutOf(buildSpotifyModel([], 10_000))).toEqual({ totalMs: 0, bars: [], overlaps: [] })
  })
})

describe('cursorPct and classMsAt', () => {
  const layout = layoutOf(model)
  it('draws no cursor for none, and keeps one inside the strip', () => {
    expect(cursorPct(layout, null)).toBeNull()
    expect(cursorPct(layout, 290_000)).toBeCloseTo(50)
    expect(cursorPct(layout, -5)).toBe(0)
    expect(cursorPct(layout, 9_999_999)).toBe(100)
  })

  it('maps a click inside a bar to a class instant inside it', () => {
    const bar = { startMs: 190_000, endMs: 390_000 }
    expect(classMsAt(bar, 0)).toBe(190_000)
    expect(classMsAt(bar, 0.5)).toBe(290_000)
    expect(classMsAt(bar, 7)).toBe(390_000)
    expect(classMsAt(bar, Number.NaN)).toBe(190_000)
  })
})

describe('playTarget', () => {
  it('plays the only song outside an overlap, from its own position', () => {
    expect(playTarget(model, 100_000)).toEqual({ piece: 0, sourceMs: 100_000, classMs: 100_000 })
    expect(playTarget(model, 250_000)).toEqual({ piece: 1, sourceMs: 60_000, classMs: 250_000 })
  })

  it('in an overlap the incoming song is the default and the outgoing one can be chosen', () => {
    expect(playTarget(model, 195_000)).toEqual({ piece: 1, sourceMs: 5_000, classMs: 195_000 })
    expect(playTarget(model, 195_000, 'incoming')?.piece).toBe(1)
    expect(playTarget(model, 195_000, 'outgoing')).toEqual({ piece: 0, sourceMs: 195_000, classMs: 195_000 })
  })

  it('plays nothing where no song is', () => {
    expect(playTarget(model, 600_000)).toBeNull()
    expect(playTarget(model, -1)).toBeNull()
  })
})
