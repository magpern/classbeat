import type { EditOp } from '../../api/classEdits'
import type { Timeline } from '../../api/studioOutputs'
import { useT } from '../../i18n/I18n'
import { actionsFor } from '../../lib/editor/blockActions'
import type { EditorBlock } from '../../lib/editor/blocks'
import { attributesLocked, timingLocked, type BlockMarks } from '../../lib/editor/overlayMarks'
import { zoneName } from '../../lib/profile'
import { clock } from '../../lib/workout'
import { Notice } from '../forms'
import { BlockAttributes } from './BlockAttributes'
import { BlockMusic } from './BlockMusic'
import { BlockStructure } from './BlockStructure'

interface Props {
  blocks: EditorBlock[]
  marks: BlockMarks[]
  timeline: Timeline
  index: number
  busy: boolean
  onEdit: (op: EditOp) => void
  onAudition: (classMs: number) => void
}

/** Everything one block can do, in three groups: its timing, its settings, its tie to the music. */
export function BlockInspector({ blocks, marks, timeline, index, busy, onEdit, onAudition }: Props) {
  const t = useT()
  const block = blocks[index]
  const mark = marks[index]
  if (block === undefined || mark === undefined) return null
  const actions = actionsFor(blocks, marks, index)
  return (
    <section className="flex flex-col gap-4 rounded-md border border-line bg-surface p-4" aria-label={t('editor.block.label', { n: index + 1 })} data-testid="block-inspector">
      <h3 className="m-0 font-display text-lg font-bold">
        {t('editor.block.title', { n: index + 1, from: clock(block.startMs / 1000), to: clock(block.endMs / 1000), zone: block.zone, name: zoneName(block.zone) })}
      </h3>
      {block.ftpTest ? <Notice>{t('editor.block.ftp')}</Notice> : null}
      {timingLocked(mark.lock) ? <Notice>{t('editor.block.timingLocked')}</Notice> : null}
      {attributesLocked(mark.lock) ? <Notice>{t('editor.block.attributesLocked')}</Notice> : null}
      <BlockStructure blocks={blocks} block={block} actions={actions} busy={busy} onEdit={onEdit} key={`s-${String(block.index)}-${String(block.sec)}`} />
      <BlockAttributes block={block} marks={mark} enabled={actions.setAttributes && !busy} onEdit={onEdit} key={`a-${String(block.index)}-${JSON.stringify([block.rpm, block.cue])}`} />
      <BlockMusic blocks={blocks} block={block} marks={mark} timeline={timeline} busy={busy} onEdit={onEdit} onAudition={onAudition} />
    </section>
  )
}
