import type { EditRecord, LockScope } from '../../api/classEdits'
import type { EditorBlock } from './blocks'

// The overlay's records as marks on blocks. A record names its block by segment kind and the
// span it had; the block it means now is the one of that kind with the greatest class-time
// overlap, if that is at least half the span (backend `edit_overlay_policy.match`). Pure.

const MIN_OVERLAP = 0.5

/** The block a record names now, by flat index; null: none (the record is unapplied). */
export function matchBlock(record: EditRecord, blocks: readonly EditorBlock[]): number | null {
  if (record.span === null || record.segmentKind === null) return null
  const [from, to] = record.span
  let best: { overlap: number; index: number } | null = null
  for (const block of blocks) {
    if (block.segmentKind !== record.segmentKind) continue
    const overlap = Math.min(block.endMs, to) - Math.max(block.startMs, from)
    if (overlap > 0 && (best === null || overlap > best.overlap)) best = { overlap, index: block.index }
  }
  return best === null || best.overlap < MIN_OVERLAP * (to - from) ? null : best.index
}

export interface OverrideMark {
  /** The instructor's value. */
  value: unknown
  /** The model's value it replaced: what "Use AI value" restores. */
  aiValue: unknown
}

export interface BlockMarks {
  lock: LockScope | null
  /** By field: an attribute, or `boundary` (the block's end, in ms of class time). */
  overrides: Partial<Record<string, OverrideMark>>
  anchors: Partial<Record<'start' | 'end', EditRecord>>
}

const SCOPES: readonly string[] = ['all', 'timing', 'attributes']

export function marksByBlock(blocks: readonly EditorBlock[], records: readonly EditRecord[]): BlockMarks[] {
  const marks: BlockMarks[] = blocks.map(() => ({ lock: null, overrides: {}, anchors: {} }))
  for (const record of records) {
    const at = matchBlock(record, blocks)
    const mark = at === null ? undefined : marks[at]
    if (mark === undefined) continue
    if (record.type === 'lock' && record.scope !== null && SCOPES.includes(record.scope)) mark.lock = record.scope as LockScope
    else if (record.type === 'override' && record.field !== null) mark.overrides[record.field] = { value: record.value, aiValue: record.aiValue }
    else if (record.type === 'anchor' && record.edge !== null) mark.anchors[record.edge] = record
  }
  return marks
}

/** An attribute edit is blocked by a lock of scope `all` or `attributes`. */
export function attributesLocked(lock: LockScope | null): boolean {
  return lock === 'all' || lock === 'attributes'
}

/** A timing edit (drag, move, split, merge) is blocked by a lock of scope `all` or `timing`. */
export function timingLocked(lock: LockScope | null): boolean {
  return lock === 'all' || lock === 'timing'
}

/** The pin records by piece and edge. */
export function pinOf(records: readonly EditRecord[], piece: number, edge: 'start' | 'end'): EditRecord | null {
  return records.find((r) => r.type === 'pin' && r.piece === piece && r.edge === edge) ?? null
}
