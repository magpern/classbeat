import { useId, useState } from 'react'
import type { MixSuggestion, MixWindow } from '../../api/studioMix'
import type { Named } from '../../api/studioSongs'
import { useT } from '../../i18n/I18n'
import { Button } from '../ui/button'
import { Switch } from '../ui/switch'
import {
  backToSuggestion,
  fullOnlyPositions,
  hardCut,
  keepWhole,
  setPositions,
  type MixState,
} from './mixChoices'
import { StudioMixRow } from './StudioMixRow'

/**
 * The DJ mix's choices (ADR 0036 amendment B, B2, B3 and B7): the switches **Blend transitions**
 * and **Trim songs**, one row per approved song, and **All songs** / **None** with the actions on
 * a selection. Everything here is local: nothing is applied until the user presses Apply these
 * choices (`StudioOutputs`), which makes a new draft.
 */
export function StudioMixChoices({
  suggestions,
  names,
  state,
  windows,
  disabled,
  onChange,
}: {
  suggestions: readonly MixSuggestion[]
  names: ReadonlyMap<number, Named>
  state: MixState
  windows: readonly MixWindow[] | null
  disabled: boolean
  onChange: (next: MixState) => void
}) {
  const t = useT()
  const id = useId()
  const [picked, setPicked] = useState<readonly number[]>([])
  const all = suggestions.map((s) => s.position)
  const selected = picked.filter((n) => all.includes(n))
  const whole = fullOnlyPositions(suggestions)
  const none = selected.length === 0
  const bulk = (label: string, apply: (positions: readonly number[]) => MixState) => (
    <Button variant="secondary" disabled={disabled || none} onClick={() => { onChange(apply(selected)) }}>
      {label}
    </Button>
  )
  const sw = (key: 'blend' | 'trim', title: string, desc: string) => (
    <div className="flex items-start gap-3">
      <Switch
        id={`${id}-${key}`}
        checked={state[key]}
        disabled={disabled}
        onCheckedChange={(on) => {
          onChange({ ...state, [key]: on })
        }}
      />
      <label htmlFor={`${id}-${key}`} className="cursor-pointer">
        <span className="block font-medium text-text">{title}</span>
        <span className="block text-[0.9375rem] text-muted">{desc}</span>
      </label>
    </div>
  )
  return (
    <div className="flex flex-col gap-3" data-testid="mix-choices" role="group" aria-labelledby={`${id}-title`}>
      <h4 id={`${id}-title`} className="m-0 font-display text-base font-bold">
        {t('studio.mix.title')}
      </h4>
      {sw('blend', t('studio.mix.blend'), t('studio.mix.blend.desc'))}
      {sw('trim', t('studio.mix.trim'), t('studio.mix.trim.desc'))}
      {suggestions.length > 0 ? (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="secondary" disabled={disabled} onClick={() => { setPicked(all) }}>
              {t('studio.mix.all')}
            </Button>
            <Button variant="secondary" disabled={disabled} onClick={() => { setPicked([]) }}>
              {t('studio.mix.none')}
            </Button>
            <span className="text-[0.9375rem] text-muted" role="status">
              {t('studio.mix.selected', { count: selected.length })}
            </span>
          </div>
          <div className="flex flex-wrap gap-2">
            {state.trim ? bulk(t('studio.mix.bulk.keepWhole'), (p) => keepWhole(state, p.filter((n) => !whole.includes(n)))) : null}
            {state.blend ? bulk(t('studio.mix.bulk.hardCut'), (p) => hardCut(state, p)) : null}
            {bulk(t('studio.mix.bulk.suggestion'), (p) => backToSuggestion(state, p))}
          </div>
          <ol className="m-0 flex list-none flex-col gap-2 p-0" aria-label={t('studio.mix.songs')}>
            {suggestions.map((s) => (
              <StudioMixRow
                key={s.position}
                suggestion={s}
                name={names.get(s.position)}
                state={state}
                selected={selected.includes(s.position)}
                windows={windows}
                disabled={disabled}
                onSelect={(on) => {
                  setPicked(on ? [...selected, s.position] : selected.filter((n) => n !== s.position))
                }}
                onToggle={(toggle, on) => {
                  onChange(setPositions(state, toggle, [s.position], on))
                }}
              />
            ))}
          </ol>
        </>
      ) : null}
    </div>
  )
}
