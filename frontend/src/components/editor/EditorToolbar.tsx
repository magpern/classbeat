import { Redo2, Undo2 } from 'lucide-react'
import { useState } from 'react'
import type { EditOp } from '../../api/classEdits'
import { useT } from '../../i18n/I18n'
import { TextInput } from '../newclass/inputs'
import { Button } from '../ui/button'

interface Props {
  canUndo: boolean
  canRedo: boolean
  hasOverlay: boolean
  totalSec: number
  busy: boolean
  onUndo: () => void
  onRedo: () => void
  onEdit: (op: EditOp) => void
}

/** Undo, redo, the class length and "Clear all edits". */
export function EditorToolbar({ canUndo, canRedo, hasOverlay, totalSec, busy, onUndo, onRedo, onEdit }: Props) {
  const t = useT()
  const [minutes, setMinutes] = useState(String(Math.round(totalSec / 60)))
  const wanted = Math.round(Number(minutes) * 60)
  const valid = minutes !== '' && Number.isFinite(wanted) && wanted >= 60 && wanted <= 4 * 3600 && wanted !== totalSec
  const [confirming, setConfirming] = useState(false)
  return (
    <div className="flex flex-col gap-3" data-testid="editor-toolbar">
      <div className="flex flex-wrap items-end gap-2">
        <Button variant="secondary" disabled={!canUndo || busy} onClick={onUndo}>
          <Undo2 aria-hidden="true" className="size-4" />
          {t('editor.undo')}
        </Button>
        <Button variant="secondary" disabled={!canRedo || busy} onClick={onRedo}>
          <Redo2 aria-hidden="true" className="size-4" />
          {t('editor.redo')}
        </Button>
        <TextInput
          label={t('editor.duration.label')}
          hint={t('editor.duration.hint')}
          type="number"
          inputMode="numeric"
          min={1}
          step={1}
          value={minutes}
          onChange={(event) => {
            setMinutes(event.target.value)
          }}
          className="w-28"
        />
        <Button variant="secondary" disabled={!valid || busy} onClick={() => { onEdit({ op: 'duration', total_sec: wanted }) }}>
          {t('editor.duration.set')}
        </Button>
      </div>
      {confirming ? (
        <div className="flex flex-wrap items-center gap-2" role="alertdialog" aria-label={t('editor.clear.confirmLabel')} data-testid="clear-confirm">
          <p className="m-0 text-[0.9375rem] text-text">{t('editor.clear.confirm')}</p>
          <Button
            onClick={() => {
              setConfirming(false)
              onEdit({ op: 'clear_all' })
            }}
          >
            {t('editor.clear.yes')}
          </Button>
          <Button variant="ghost" onClick={() => { setConfirming(false) }}>
            {t('common.cancel')}
          </Button>
        </div>
      ) : (
        <div>
          <Button variant="ghost" disabled={!hasOverlay || busy} onClick={() => { setConfirming(true) }}>
            {t('editor.clear.all')}
          </Button>
        </div>
      )}
    </div>
  )
}
