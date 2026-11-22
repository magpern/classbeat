import { useState } from 'react'
import type { Edge, EditOp, LockScope } from '../../api/classEdits'
import type { Timeline } from '../../api/studioOutputs'
import { decimal } from '../../i18n/format'
import { useT } from '../../i18n/I18n'
import type { EditorBlock } from '../../lib/editor/blocks'
import type { BlockMarks } from '../../lib/editor/overlayMarks'
import { snapCandidates } from '../../lib/editor/snap'
import { clock } from '../../lib/workout'
import { SelectInput } from '../newclass/inputs'
import { Button } from '../ui/button'
import { Chip } from '../ui/chip'

interface Props {
  blocks: EditorBlock[]
  block: EditorBlock
  marks: BlockMarks
  timeline: Timeline
  busy: boolean
  onEdit: (op: EditOp) => void
  onAudition: (classMs: number) => void
}

const SCOPES: readonly LockScope[] = ['all', 'timing', 'attributes']

/** The block's lock, its anchors ("Snap to music") and the silent cursor: what ties the block to the music or keeps automation off it. */
export function BlockMusic({ blocks, block, marks, timeline, busy, onEdit, onAudition }: Props) {
  const t = useT()
  const [scope, setScope] = useState<LockScope>('all')
  const [picking, setPicking] = useState<Edge | null>(null)
  // The edges a block can be anchored on: the first block has no start to move, the last no end.
  const edges: Edge[] = [block.index > 0 ? 'start' : null, block.index < blocks.length - 1 ? 'end' : null].filter((e): e is Edge => e !== null)
  const candidates = picking === null ? [] : snapCandidates(timeline, (picking === 'start' ? block.startMs : block.endMs) / 1000)
  return (
    <fieldset className="m-0 flex flex-col gap-3 border-0 p-0" disabled={busy}>
      <legend className="mb-2 font-display text-base font-bold">{t('editor.music.title')}</legend>
      <div className="flex flex-wrap items-end gap-2">
        {marks.lock === null ? (
          <>
            <SelectInput
              label={t('editor.lock.scope')}
              value={scope}
              onChange={(event) => {
                setScope(event.target.value as LockScope)
              }}
            >
              {SCOPES.map((s) => (
                <option key={s} value={s}>
                  {t(`editor.lock.scope.${s}`)}
                </option>
              ))}
            </SelectInput>
            <Button variant="secondary" onClick={() => { onEdit({ op: 'lock', block: block.index, scope }) }}>
              {t('editor.lock.lock')}
            </Button>
          </>
        ) : (
          <>
            <Chip tone="warn" data-testid="lock-chip">
              {t(`editor.lock.is.${marks.lock}`)}
            </Chip>
            <Button variant="secondary" onClick={() => { onEdit({ op: 'unlock', block: block.index }) }}>
              {t('editor.lock.unlock')}
            </Button>
          </>
        )}
      </div>
      <div className="flex flex-col gap-2">
        {edges.map((edge) => {
          const anchor = marks.anchors[edge]
          return (
            <div key={edge} className="flex flex-wrap items-center gap-2" data-testid={`anchor-${edge}`}>
              {anchor === undefined ? (
                <Button variant="secondary" aria-pressed={picking === edge} onClick={() => { setPicking(picking === edge ? null : edge) }}>
                  {t(`editor.snap.${edge}`)}
                </Button>
              ) : (
                <>
                  <Chip>{t(`editor.snap.anchored.${edge}`, { song: (anchor.piece ?? -1) + 1 })}</Chip>
                  <Button variant="secondary" onClick={() => { onEdit({ op: 'remove_anchor', block: block.index, edge }) }}>
                    {t(`editor.snap.remove.${edge}`)}
                  </Button>
                </>
              )}
            </div>
          )
        })}
        {picking !== null ? (
          candidates.length === 0 ? (
            <p className="m-0 text-[0.9375rem] text-muted" data-testid="snap-none">{t('editor.snap.none')}</p>
          ) : (
            <ul className="m-0 flex list-none flex-wrap gap-2 p-0" aria-label={t('editor.snap.candidates')}>
              {candidates.map((c) => (
                <li key={`${String(c.piece)}-${String(c.sourceMs)}`}>
                  <Button
                    variant="secondary"
                    onClick={() => {
                      setPicking(null)
                      onEdit({ op: 'anchor', block: block.index, edge: picking, event: { piece: c.piece, source_ms: c.sourceMs, kind: c.kind } })
                    }}
                  >
                    {t('editor.snap.candidate', { time: clock(c.classSec), song: c.piece + 1, delta: decimal(c.deltaSec, 1) })}
                  </Button>
                </li>
              ))}
            </ul>
          )
        ) : null}
      </div>
      <div>
        <Button variant="ghost" onClick={() => { onAudition(block.startMs) }}>
          {t('editor.music.showCursor')}
        </Button>
      </div>
    </fieldset>
  )
}
