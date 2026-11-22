import { describe, expect, it } from 'vitest'
import { attributesLocked, marksByBlock, matchBlock, pinOf, timingLocked } from './overlayMarks'
import { blocksOf, record } from './editorFixtures'

const blocks = blocksOf()

describe('matchBlock', () => {
  it('names the block of the same kind with the greatest overlap, at least half the span', () => {
    expect(matchBlock(record({ segmentKind: 'main', span: [60_000, 180_000] }), blocks)).toBe(1)
    // After a drag the block grew to [60, 200); the record's older span [60, 180) still matches it.
    expect(matchBlock(record({ segmentKind: 'main', span: [170_000, 250_000] }), blocks)).toBe(2)
  })

  it('does not match another kind, an overlap under half, or a record without a span', () => {
    expect(matchBlock(record({ segmentKind: 'cooldown', span: [60_000, 180_000] }), blocks)).toBeNull()
    expect(matchBlock(record({ segmentKind: 'main', span: [150_000, 240_000] }), blocks)).toBe(2)
    expect(matchBlock(record({ segmentKind: 'main', span: [175_000, 500_000] }), blocks)).toBeNull()
    expect(matchBlock(record({ segmentKind: 'main', span: null }), blocks)).toBeNull()
  })
})

describe('marksByBlock', () => {
  const records = [
    record({ type: 'lock', segmentKind: 'main', span: [60_000, 180_000], scope: 'timing' }),
    record({ type: 'override', segmentKind: 'main', span: [60_000, 180_000], field: 'zone', value: 4, aiValue: 3 }),
    record({ type: 'anchor', segmentKind: 'main', span: [60_000, 180_000], edge: 'end', piece: 0, value: 170_000 }),
    record({ type: 'lock', segmentKind: 'warmup', span: [0, 60_000], scope: 'bogus' }),
  ]

  it('puts locks, overrides and anchors on the block they name', () => {
    const marks = marksByBlock(blocks, records)
    expect(marks[1]?.lock).toBe('timing')
    expect(marks[1]?.overrides['zone']).toEqual({ value: 4, aiValue: 3 })
    expect(marks[1]?.anchors.end?.piece).toBe(0)
    expect(marks[0]?.lock).toBeNull() // an unknown scope is not guessed at
    expect(marks[2]?.lock).toBeNull()
  })

  it('reads the lock scopes: timing blocks timing, attributes blocks attributes, all both', () => {
    expect([timingLocked('timing'), timingLocked('attributes'), timingLocked('all'), timingLocked(null)]).toEqual([true, false, true, false])
    expect([attributesLocked('timing'), attributesLocked('attributes'), attributesLocked('all'), attributesLocked(null)]).toEqual([false, true, true, false])
  })
})

describe('pinOf', () => {
  it('finds the pin of a piece and an edge', () => {
    const pins = [record({ type: 'pin', piece: 1, edge: 'end', value: 200, confirmed: false })]
    expect(pinOf(pins, 1, 'end')?.value).toBe(200)
    expect(pinOf(pins, 1, 'start')).toBeNull()
    expect(pinOf(pins, 0, 'end')).toBeNull()
  })
})
