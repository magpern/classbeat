import { useState } from 'react'
import type { EditOp } from '../../api/classEdits'
import { useT } from '../../i18n/I18n'
import { dragOp, dragRange, mergeNeedsConfirm, moveOp, splitOp, type BlockActions } from '../../lib/editor/blockActions'
import type { EditorBlock } from '../../lib/editor/blocks'
import { TextInput } from '../newclass/inputs'
import { Button } from '../ui/button'

interface Props {
  blocks: EditorBlock[]
  block: EditorBlock
  actions: BlockActions
  busy: boolean
  onEdit: (op: EditOp) => void
}

/** The timing of one block: move its end (the keyboard way to a drag), swap it, split it, merge it with the next. */
export function BlockStructure({ blocks, block, actions, busy, onEdit }: Props) {
  const t = useT()
  const [nudge, setNudge] = useState('1')
  const [offset, setOffset] = useState(String(Math.max(1, Math.floor(block.sec / 2))))
  const [confirming, setConfirming] = useState(false)
  const range = dragRange(blocks, block.index)
  const move = dragOp(blocks, block.index, Number(nudge))
  const split = splitOp(blocks, block.index, Number(offset))
  const next = blocks[block.index + 1]
  return (
    <fieldset className="m-0 flex flex-col gap-3 border-0 p-0" disabled={busy}>
      <legend className="mb-2 font-display text-base font-bold">{t('editor.timing.title')}</legend>
      <div className="flex flex-wrap items-end gap-2">
        <TextInput
          label={t('editor.timing.nudge')}
          hint={t('editor.timing.nudgeHint', { earlier: range.earlier, later: range.later })}
          type="number"
          inputMode="numeric"
          step={1}
          value={nudge}
          onChange={(event) => {
            setNudge(event.target.value)
          }}
          className="w-28"
        />
        <Button
          variant="secondary"
          disabled={!actions.drag || move === null}
          onClick={() => {
            if (move !== null) onEdit(move)
          }}
        >
          {t('editor.timing.moveEnd')}
        </Button>
      </div>
      <div className="flex flex-wrap gap-2">
        <Button variant="secondary" disabled={!actions.moveEarlier} onClick={() => { onEdit(moveOp(block.index, -1)) }}>
          {t('editor.timing.earlier')}
        </Button>
        <Button variant="secondary" disabled={!actions.moveLater} onClick={() => { onEdit(moveOp(block.index, 1)) }}>
          {t('editor.timing.later')}
        </Button>
      </div>
      <div className="flex flex-wrap items-end gap-2">
        <TextInput
          label={t('editor.timing.splitAt')}
          type="number"
          inputMode="numeric"
          step={1}
          min={1}
          value={offset}
          onChange={(event) => {
            setOffset(event.target.value)
          }}
          className="w-28"
        />
        <Button variant="secondary" disabled={!actions.split || split === null} onClick={() => { if (split !== null) onEdit(split) }}>
          {t('editor.timing.split')}
        </Button>
      </div>
      {confirming && next !== undefined ? (
        <div className="flex flex-col gap-2" role="alertdialog" aria-label={t('editor.timing.mergeConfirmLabel')} data-testid="merge-confirm">
          <p className="m-0 text-[0.9375rem] text-text">{t('editor.timing.mergeConfirm', { a: block.zone, b: next.zone })}</p>
          <div className="flex flex-wrap gap-2">
            <Button
              onClick={() => {
                setConfirming(false)
                onEdit({ op: 'merge', block: block.index })
              }}
            >
              {t('editor.timing.mergeYes')}
            </Button>
            <Button variant="ghost" onClick={() => { setConfirming(false) }}>
              {t('common.cancel')}
            </Button>
          </div>
        </div>
      ) : (
        <div>
          <Button
            variant="secondary"
            disabled={!actions.merge}
            onClick={() => {
              if (mergeNeedsConfirm(blocks, block.index)) setConfirming(true)
              else onEdit({ op: 'merge', block: block.index })
            }}
          >
            {t('editor.timing.merge')}
          </Button>
        </div>
      )}
    </fieldset>
  )
}
