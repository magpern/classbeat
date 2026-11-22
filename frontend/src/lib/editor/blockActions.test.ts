import { describe, expect, it } from 'vitest'
import { actionsFor, blockAt, dragOp, dragRange, mergeNeedsConfirm, splitOp } from './blockActions'
import { marksByBlock } from './overlayMarks'
import { blocksOf, record } from './editorFixtures'

const blocks = blocksOf()
const none = marksByBlock(blocks, [])

describe('actionsFor', () => {
  it('allows drag, move and merge inside a segment and not across one', () => {
    expect(actionsFor(blocks, none, 1)).toMatchObject({ drag: false, moveEarlier: false, moveLater: true, merge: false }) // next is the FTP test
    expect(actionsFor(blocks, none, 0)).toMatchObject({ drag: true, moveLater: false, merge: false }) // next block is another segment
    expect(actionsFor(blocks, none, 4)).toMatchObject({ drag: false, merge: false, moveEarlier: false })
  })

  it('stops an FTP test block from being dragged, split or merged', () => {
    expect(actionsFor(blocks, none, 2)).toMatchObject({ drag: false, split: false, merge: false })
  })

  it('stops a timing lock from timing edits and an attribute lock from attribute edits', () => {
    const timing = marksByBlock(blocks, [record({ type: 'lock', segmentKind: 'main', span: [60_000, 180_000], scope: 'timing' })])
    expect(actionsFor(blocks, timing, 1)).toMatchObject({ drag: false, split: false, moveLater: false, setAttributes: true })
    const attrs = marksByBlock(blocks, [record({ type: 'lock', segmentKind: 'main', span: [60_000, 180_000], scope: 'attributes' })])
    expect(actionsFor(blocks, attrs, 1)).toMatchObject({ split: true, setAttributes: false })
    expect(actionsFor(blocks, timing, 0).drag).toBe(false) // it would move the start of the locked block after it
  })

  it('stops a split when a half would be under the minimum', () => {
    expect(actionsFor(blocks, none, 3).split).toBe(true) // 60 s game block: 2 x 10 s fits
    const tiny = blocksOf({ segments: [{ name: 'a', kind: 'main', intervals: [{ sec: 1, zone: 1, rpm: 'beat', standing: false }] }] })
    expect(actionsFor(tiny, marksByBlock(tiny, []), 0).split).toBe(false)
  })

  it('has nothing for a block that does not exist', () => {
    expect(Object.values(actionsFor(blocks, none, 99)).every((v) => !v)).toBe(true)
  })
})

describe('drag and split edits', () => {
  it('knows how far the boundary can move: the minimum of each side', () => {
    expect(dragRange(blocks, 0)).toEqual({ earlier: 59, later: 119 })
    expect(dragRange(blocks, 3)).toEqual({ earlier: 50, later: 20 }) // the game block keeps 10 s, the block after it too
    expect(dragRange(blocks, 4)).toEqual({ earlier: 0, later: 0 })
  })

  it('clamps a drag to the range and sends whole seconds', () => {
    expect(dragOp(blocks, 0, 9.6)).toEqual({ op: 'drag', block: 0, delta_sec: 10 })
    expect(dragOp(blocks, 0, 1000)).toEqual({ op: 'drag', block: 0, delta_sec: 119 })
    expect(dragOp(blocks, 0, -1000)).toEqual({ op: 'drag', block: 0, delta_sec: -59 })
    expect(dragOp(blocks, 0, 0.2)).toBeNull()
  })

  it('refuses a split that leaves a half under the minimum', () => {
    expect(splitOp(blocks, 1, 60)).toEqual({ op: 'split', block: 1, offset_sec: 60 })
    expect(splitOp(blocks, 1, 0)).toBeNull()
    expect(splitOp(blocks, 1, 120)).toBeNull()
    expect(splitOp(blocks, 3, 5)).toBeNull() // a game block's halves are at least 10 s
    expect(splitOp(blocks, 1, 1.5)).toBeNull()
  })

  it('asks before a merge only when the zones differ', () => {
    expect(mergeNeedsConfirm(blocks, 1)).toBe(true)
    const same = blocksOf({ segments: [{ name: 'a', kind: 'main', intervals: [{ sec: 30, zone: 2, rpm: 'beat', standing: false }, { sec: 30, zone: 2, rpm: 'beat', standing: false }] }] })
    expect(mergeNeedsConfirm(same, 0)).toBe(false)
  })

  it('finds the block at a class second, the last one at or past the end', () => {
    expect(blockAt(blocks, 0)?.index).toBe(0)
    expect(blockAt(blocks, 60)?.index).toBe(1)
    expect(blockAt(blocks, 359)?.index).toBe(4)
    expect(blockAt(blocks, 9999)?.index).toBe(4)
  })
})
