import { useState } from 'react'
import type { AttributeField, EditOp } from '../../api/classEdits'
import { useT } from '../../i18n/I18n'
import type { EditorBlock } from '../../lib/editor/blocks'
import type { BlockMarks } from '../../lib/editor/overlayMarks'
import { fieldName, valueText } from '../../lib/editor/valueText'
import { gameLabel } from '../../lib/games'
import { zoneName } from '../../lib/profile'
import { Button } from '../ui/button'
import { Chip } from '../ui/chip'
import { SelectInput, TextInput } from '../newclass/inputs'

const GAMES = ['racetrack', 'power_pull', 'fast_lane', 'overflow'] as const
const ZONES = [1, 2, 3, 4, 5, 6, 7] as const

interface Props {
  block: EditorBlock
  marks: BlockMarks
  /** False: the block is attribute-locked or the page is busy. */
  enabled: boolean
  onEdit: (op: EditOp) => void
}

/** "Your edit kept (AI suggested ...)" with its way back, for one field of the block. */
function Badge({ field, marks, enabled, onEdit, block }: { field: AttributeField; marks: BlockMarks; enabled: boolean; onEdit: (op: EditOp) => void; block: number }) {
  const t = useT()
  const kept = marks.overrides[field]
  if (kept === undefined) return null
  return (
    <p className="m-0 flex flex-wrap items-center gap-2 text-[0.9375rem]" data-testid={`override-${field}`}>
      <Chip tone="warn">{t('editor.override.kept', { value: valueText(field, kept.aiValue) })}</Chip>
      <Button
        variant="secondary"
        disabled={!enabled}
        aria-label={t('editor.override.useAi.label', { field: fieldName(field) })}
        onClick={() => {
          onEdit({ op: 'use_ai_value', block, field })
        }}
      >
        {t('editor.override.useAi')}
      </Button>
    </p>
  )
}

/** The block's zone, cadence, standing, cue and game: each is one edit, and never the timing or the music. */
export function BlockAttributes({ block, marks, enabled, onEdit }: Props) {
  const t = useT()
  const [from, setFrom] = useState(block.rpm === 'beat' ? '' : String(block.rpm[0]))
  const [to, setTo] = useState(block.rpm === 'beat' ? '' : String(block.rpm[1]))
  const [cue, setCue] = useState(block.cue)
  const set = (field: AttributeField, value: unknown) => {
    onEdit({ op: 'set', block: block.index, field, value })
  }
  const lo = Number(from)
  const hi = Number(to)
  const rpmOk = from !== '' && to !== '' && Number.isInteger(lo) && Number.isInteger(hi) && lo >= 0 && hi <= 200 && lo <= hi
  const badge = (field: AttributeField) => <Badge field={field} marks={marks} enabled={enabled} onEdit={onEdit} block={block.index} />
  return (
    <fieldset className="m-0 flex flex-col gap-3 border-0 p-0" disabled={!enabled}>
      <legend className="mb-2 font-display text-base font-bold">{t('editor.attrs.title')}</legend>
      <SelectInput
        label={t('editor.field.zone')}
        value={block.zone}
        onChange={(event) => {
          set('zone', Number(event.target.value))
        }}
      >
        {ZONES.map((z) => (
          <option key={z} value={z}>
            {t('class.zone.line', { zone: z, name: zoneName(z) })}
          </option>
        ))}
      </SelectInput>
      {badge('zone')}
      <div className="flex flex-wrap items-end gap-2">
        <TextInput label={t('editor.rpm.from')} type="number" inputMode="numeric" min={0} max={200} value={from} onChange={(event) => { setFrom(event.target.value) }} className="w-28" />
        <TextInput label={t('editor.rpm.to')} type="number" inputMode="numeric" min={0} max={200} value={to} onChange={(event) => { setTo(event.target.value) }} className="w-28" />
        <Button variant="secondary" disabled={!rpmOk} onClick={() => { set('rpm', [lo, hi]) }}>
          {t('editor.rpm.set')}
        </Button>
        <Button variant="secondary" disabled={block.rpm === 'beat'} onClick={() => { set('rpm', 'beat') }}>
          {t('editor.rpm.beat')}
        </Button>
      </div>
      {badge('rpm')}
      <label className="flex min-h-11 items-center gap-2 text-[0.9375rem] text-text">
        <input type="checkbox" checked={block.standing} onChange={(event) => { set('standing', event.target.checked) }} />
        {t('editor.field.standing')}
      </label>
      {badge('standing')}
      <SelectInput
        label={t('editor.field.game')}
        value={block.game ?? ''}
        onChange={(event) => {
          set('game', event.target.value === '' ? null : event.target.value)
        }}
      >
        <option value="">{t('editor.value.noGame')}</option>
        {GAMES.map((g) => (
          <option key={g} value={g}>
            {gameLabel(g)}
          </option>
        ))}
      </SelectInput>
      {badge('game')}
      <div className="flex flex-wrap items-end gap-2">
        <TextInput label={t('editor.field.cue')} maxLength={120} value={cue} onChange={(event) => { setCue(event.target.value) }} className="min-w-56" />
        <Button variant="secondary" disabled={cue === block.cue} onClick={() => { set('cue', cue) }}>
          {t('editor.cue.set')}
        </Button>
      </div>
      {badge('cue')}
    </fieldset>
  )
}
