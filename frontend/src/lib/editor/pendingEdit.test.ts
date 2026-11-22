import { describe, expect, it } from 'vitest'
import type { EditOp } from '../../api/classEdits'
import { pendingOf, stillApplies } from './pendingEdit'
import { CONTENT, blocksOf } from './editorFixtures'

const blocks = blocksOf()
const pieces = ['First song', 'Second song']
const drag: EditOp = { op: 'drag', block: 0, delta_sec: 10 }

describe('pending edit across a stale 409', () => {
  it('applies again when the reloaded workout still has the same blocks', () => {
    expect(stillApplies(pendingOf(drag, blocks, pieces), blocksOf(), pieces)).toBe(true)
  })

  it('does not apply when another tab changed the targeted block or the next one', () => {
    const pending = pendingOf(drag, blocks, pieces)
    const changedFirst = { ...CONTENT, segments: [{ ...CONTENT.segments[0], intervals: [{ sec: 70, zone: 2, rpm: [80, 90], standing: false, cue: '' }] }, ...CONTENT.segments.slice(1)] }
    expect(stillApplies(pending, blocksOf(changedFirst), pieces)).toBe(false)
    const zoneOfNext = { ...CONTENT, segments: [CONTENT.segments[0], { ...CONTENT.segments[1], intervals: [{ ...(CONTENT.segments[1]?.intervals[0] ?? {}), zone: 7 }, ...(CONTENT.segments[1]?.intervals.slice(1) ?? [])] }, CONTENT.segments[2]] }
    expect(stillApplies(pending, blocksOf(zoneOfNext), pieces)).toBe(false)
  })

  it('does not apply when the block it meant is gone', () => {
    const op: EditOp = { op: 'split', block: 4, offset_sec: 10 }
    const shorter = { ...CONTENT, segments: CONTENT.segments.slice(0, 2) }
    expect(stillApplies(pendingOf(op, blocks, pieces), blocksOf(shorter), pieces)).toBe(false)
  })

  it('offers a class-wide edit always', () => {
    expect(stillApplies(pendingOf({ op: 'duration', total_sec: 300 }, blocks, pieces), blocksOf(), pieces)).toBe(true)
    expect(stillApplies(pendingOf({ op: 'clear_all' }, blocks, pieces), blocksOf(), pieces)).toBe(true)
  })

  it('keeps a pin edit only while its piece index is still the same song', () => {
    const pin: EditOp = { op: 'pin_confirm', piece: 1, edge: 'end' }
    const pending = pendingOf(pin, blocks, pieces)
    expect(stillApplies(pending, blocks, pieces)).toBe(true)
    expect(stillApplies(pending, blocks, ['First song', 'Another song'])).toBe(false)
    expect(stillApplies(pending, blocks, ['First song'])).toBe(false)
  })
})
