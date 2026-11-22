import { useState } from 'react'
import type { Edge, EditOp, EditRecord, PinMode } from '../../api/classEdits'
import type { Timeline, TimelineTrack } from '../../api/studioOutputs'
import { useT } from '../../i18n/I18n'
import { pinOf } from '../../lib/editor/overlayMarks'
import { SelectInput, TextInput } from '../newclass/inputs'
import { Button } from '../ui/button'
import { Chip } from '../ui/chip'

interface RowProps {
  track: TimelineTrack
  edge: Edge
  pin: EditRecord | null
  busy: boolean
  onEdit: (op: EditOp) => void
}

const roundTo = (n: number) => Math.round(n * 100) / 100

function PinRow({ track, edge, pin, busy, onEdit }: RowProps) {
  const t = useT()
  const effective = edge === 'start' ? track.startSec : track.endSec
  const [requested, setRequested] = useState(String(roundTo(typeof pin?.value === 'number' ? pin.value : effective)))
  const [mode, setMode] = useState<PinMode>('bar')
  const sec = Number(requested)
  const valid = requested !== '' && Number.isFinite(sec) && sec >= 0 && sec <= 3600
  return (
    <li className="flex flex-col gap-2 rounded-md border border-line bg-surface p-3" data-testid={`pin-${String(track.position)}-${edge}`}>
      <p className="m-0 flex flex-wrap items-center gap-2 text-[0.9375rem] text-text">
        <span className="font-medium">{t(`editor.pins.cut.${edge}`, { n: track.position + 1, title: track.title })}</span>
        <span className="text-muted">{t('editor.pins.effective', { sec: roundTo(effective) })}</span>
        {pin === null ? null : (
          <Chip tone={pin.confirmed === true ? 'ok' : 'warn'}>
            {pin.confirmed === true ? t('editor.pins.confirmed') : t('editor.pins.unconfirmed', { sec: typeof pin.value === 'number' ? roundTo(pin.value) : 0 })}
          </Chip>
        )}
      </p>
      <div className="flex flex-wrap items-end gap-2">
        <TextInput
          label={t('editor.pins.requested')}
          type="number"
          inputMode="decimal"
          min={0}
          max={3600}
          step={0.01}
          value={requested}
          onChange={(event) => {
            setRequested(event.target.value)
          }}
          className="w-32"
        />
        <SelectInput
          label={t('editor.pins.mode')}
          value={mode}
          onChange={(event) => {
            setMode(event.target.value as PinMode)
          }}
        >
          <option value="bar">{t('editor.pins.mode.bar')}</option>
          <option value="exact">{t('editor.pins.mode.exact')}</option>
        </SelectInput>
        <Button variant="secondary" disabled={!valid || busy} onClick={() => { onEdit({ op: 'pin_set', piece: track.position, edge, requested_sec: roundTo(sec), mode }) }}>
          {t('editor.pins.set')}
        </Button>
        {pin !== null && pin.confirmed !== true ? (
          <Button disabled={busy} onClick={() => { onEdit({ op: 'pin_confirm', piece: track.position, edge }) }}>
            {t('editor.pins.confirm')}
          </Button>
        ) : null}
        {pin !== null ? (
          <Button variant="ghost" disabled={busy} onClick={() => { onEdit({ op: 'pin_clear', piece: track.position, edge }) }}>
            {t('editor.pins.clear')}
          </Button>
        ) : null}
      </div>
    </li>
  )
}

/**
 * The cut pins (ADR 0042 D5): where a song's cut is asked to be, snapped to the bar grid or kept
 * exact, and "Confirm cut" once the instructor has checked it. A pin has a requested, an effective
 * and a confirmed position; the effective one is the server's, so it is read, never computed here.
 */
export function CutPins({ timeline, records, busy, onEdit }: { timeline: Timeline; records: readonly EditRecord[]; busy: boolean; onEdit: (op: EditOp) => void }) {
  const t = useT()
  return (
    <section className="flex flex-col gap-3" aria-label={t('editor.pins.title')} data-testid="cut-pins">
      <h3 className="m-0 font-display text-lg font-bold">{t('editor.pins.title')}</h3>
      <p className="m-0 text-muted">{t('editor.pins.intro')}</p>
      <ul className="m-0 flex list-none flex-col gap-2 p-0">
        {timeline.tracks.flatMap((track) =>
          (['start', 'end'] as const).map((edge) => (
            <PinRow key={`${String(track.position)}-${edge}-${JSON.stringify(pinOf(records, track.position, edge)?.value ?? null)}`} track={track} edge={edge} pin={pinOf(records, track.position, edge)} busy={busy} onEdit={onEdit} />
          )),
        )}
      </ul>
    </section>
  )
}
