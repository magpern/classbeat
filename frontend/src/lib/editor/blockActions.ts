import type { EditOp } from '../../api/classEdits'
import { minSec, type EditorBlock } from './blocks'
import { attributesLocked, timingLocked, type BlockMarks } from './overlayMarks'

// What each block can do, and the edit a gesture makes. The server decides every edit again (a
// locked block, an FTP test, the class-length gates), so these only keep the controls honest: a
// control that would be refused is disabled, never hidden. Pure.

export interface BlockActions {
  /** Move the end of the block (and the start of the next): a drag, or the arrow keys. */
  drag: boolean
  moveEarlier: boolean
  moveLater: boolean
  split: boolean
  merge: boolean
  setAttributes: boolean
}

export function actionsFor(blocks: readonly EditorBlock[], marks: readonly BlockMarks[], index: number): BlockActions {
  const block = blocks[index]
  const next = blocks[index + 1]
  const prev = blocks[index - 1]
  const lock = marks[index]?.lock ?? null
  if (block === undefined) return { drag: false, moveEarlier: false, moveLater: false, split: false, merge: false, setAttributes: false }
  const free = !timingLocked(lock) && !block.ftpTest
  const nextFree = next !== undefined && !next.ftpTest && !timingLocked(marks[index + 1]?.lock ?? null)
  const sameNext = next !== undefined && next.segment === block.segment
  const samePrev = prev !== undefined && prev.segment === block.segment
  return {
    drag: free && nextFree,
    moveEarlier: !timingLocked(lock) && samePrev && !timingLocked(marks[index - 1]?.lock ?? null),
    moveLater: !timingLocked(lock) && sameNext && !timingLocked(marks[index + 1]?.lock ?? null),
    split: free && block.sec >= 2 * minSec(blocks, index),
    merge: free && sameNext && nextFree,
    setAttributes: !attributesLocked(lock),
  }
}

/** How far the end of block `index` may move, in whole seconds: later at most `later`, earlier at most `earlier`. */
export function dragRange(blocks: readonly EditorBlock[], index: number): { earlier: number; later: number } {
  const block = blocks[index]
  const next = blocks[index + 1]
  if (block === undefined || next === undefined) return { earlier: 0, later: 0 }
  return { earlier: Math.max(0, block.sec - minSec(blocks, index)), later: Math.max(0, next.sec - minSec(blocks, index + 1)) }
}

/** A drag of the end of block `index` by `deltaSec` (clamped to the range), or null when it moves nothing. */
export function dragOp(blocks: readonly EditorBlock[], index: number, deltaSec: number): EditOp | null {
  const { earlier, later } = dragRange(blocks, index)
  const delta = Math.max(-earlier, Math.min(later, Math.round(deltaSec)))
  return delta === 0 ? null : { op: 'drag', block: index, delta_sec: delta }
}

export function moveOp(index: number, direction: -1 | 1): EditOp {
  return { op: 'move', block: index, direction }
}

/** A split `offsetSec` into the block, or null when either half would be under the minimum. */
export function splitOp(blocks: readonly EditorBlock[], index: number, offsetSec: number): EditOp | null {
  const block = blocks[index]
  if (block === undefined || !Number.isInteger(offsetSec)) return null
  if (offsetSec < minSec(blocks, index) || block.sec - offsetSec < minSec(blocks, index)) return null
  return { op: 'split', block: index, offset_sec: offsetSec }
}

/** A merge is silent when the two blocks have the same zone; otherwise the page asks first. */
export function mergeNeedsConfirm(blocks: readonly EditorBlock[], index: number): boolean {
  const a = blocks[index]
  const b = blocks[index + 1]
  return a !== undefined && b !== undefined && a.zone !== b.zone
}

/** The block where class second `sec` falls; the last block at or past the end. */
export function blockAt(blocks: readonly EditorBlock[], sec: number): EditorBlock | undefined {
  return blocks.find((b) => sec * 1000 >= b.startMs && sec * 1000 < b.endMs) ?? blocks[blocks.length - 1]
}
