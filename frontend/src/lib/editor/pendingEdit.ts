import type { EditOp } from '../../api/classEdits'
import type { EditorBlock } from './blocks'

// The unsent edit kept across a 409 `stale_revision` (ADR 0042 D3): when the class moved under
// the page, the page reloads, keeps what the user meant and offers to apply it again. A block
// edit names a flat index, so the index of the reloaded workout may mean another block: the edit
// is kept with a snapshot of the block it targeted and offered again ONLY when the reloaded
// workout still has that same block at the same index. Pure.

export interface BlockSnapshot {
  segmentKind: string
  startMs: number
  endMs: number
  zone: number
}

export interface PendingEdit {
  op: EditOp
  /** The block (and, for a drag or merge, the next block) the edit meant, as the page showed it. */
  targets: BlockSnapshot[]
  /** For a pin edit: the label of the piece it meant (a piece is named by its play-order index). */
  piece: string | null
}

const snapshot = (b: EditorBlock): BlockSnapshot => ({ segmentKind: b.segmentKind, startMs: b.startMs, endMs: b.endMs, zone: b.zone })

/** The blocks an edit reads: its own, and the next one for a drag or a merge. */
function targetIndexes(op: EditOp): number[] {
  if (!('block' in op)) return []
  return op.op === 'drag' || op.op === 'merge' ? [op.block, op.block + 1] : [op.block]
}

function pieceOf(op: EditOp, pieces: readonly string[]): string | null {
  return 'piece' in op ? (pieces[op.piece] ?? null) : null
}

export function pendingOf(op: EditOp, blocks: readonly EditorBlock[], pieces: readonly string[]): PendingEdit {
  const targets = targetIndexes(op).flatMap((i) => {
    const block = blocks[i]
    return block === undefined ? [] : [snapshot(block)]
  })
  return { op, targets, piece: pieceOf(op, pieces) }
}

const same = (a: BlockSnapshot, b: BlockSnapshot) =>
  a.segmentKind === b.segmentKind && a.startMs === b.startMs && a.endMs === b.endMs && a.zone === b.zone

/**
 * Whether the edit still means the same thing on the reloaded blocks. An edit of the whole class
 * (duration, clear all) has no block to check and is always offered; a pin edit names a piece by index, so it
 * applies only when that index is still the same piece.
 */
export function stillApplies(pending: PendingEdit, blocks: readonly EditorBlock[], pieces: readonly string[]): boolean {
  if ('piece' in pending.op) return pending.piece !== null && pieceOf(pending.op, pieces) === pending.piece
  const indexes = targetIndexes(pending.op)
  if (indexes.length === 0) return true
  return indexes.length === pending.targets.length && indexes.every((i, n) => {
    const block = blocks[i]
    const was = pending.targets[n]
    return block !== undefined && was !== undefined && same(snapshot(block), was)
  })
}
