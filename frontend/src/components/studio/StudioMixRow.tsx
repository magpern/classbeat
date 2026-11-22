import type { MixSuggestion, MixWindow } from '../../api/studioMix'
import type { Named } from '../../api/studioSongs'
import { useT } from '../../i18n/I18n'
import { minutes } from '../../lib/songs'
import { isRepeated, type MixState } from './mixChoices'

type Toggle = 'keepWhole' | 'hardIn' | 'hardOut'

/** Whether a piece plays the whole song (within half a second of both edges). */
const FULL_ONLY = {
  timing_approximate: 'studio.mix.fullOnly.approximate',
  timing_unusable: 'studio.mix.fullOnly.unusable',
  timing_unknown: 'studio.mix.fullOnly.unknown',
} as const

const isWhole = (a: number, b: number, length: number) => a < 0.5 && b > length - 0.5

/**
 * One approved song of the DJ mix screen (ADR 0036 amendment B, B3, B5 and B7): classbeat's
 * proposal next to the full version, **Keep whole**, **Hard cut in** and **Hard cut out**, and a
 * checkbox that selects the row for the bulk actions. A song that is not `exact` says **Full song
 * only** with its reason and has no trim toggle; a repeated or looped song says that keeping it
 * whole plays it once. With Blend off there is nothing to cut hard, and with Trim off every song is
 * whole, so those toggles are not shown.
 */
export function StudioMixRow({
  suggestion,
  name,
  state,
  selected,
  windows,
  disabled,
  onSelect,
  onToggle,
}: {
  suggestion: MixSuggestion
  name: Named | undefined
  state: MixState
  selected: boolean
  /** What the preview of the current choices plays of this song; null while not known. */
  windows: readonly MixWindow[] | null
  disabled: boolean
  onSelect: (on: boolean) => void
  onToggle: (toggle: Toggle, on: boolean) => void
}) {
  const t = useT()
  const { position, lengthSec } = suggestion
  const label = name === undefined ? t('studio.mix.song.unnamed', { n: position + 1 }) : t('studio.tl.song', { title: name.title, artist: name.artist })
  const length = minutes(lengthSec)
  const plays = (pieces: readonly { startSec: number; endSec: number }[], phrase: boolean) =>
    pieces
      .map((p, n) => {
        const whole = isWhole(p.startSec, p.endSec, lengthSec)
        const said = whole
          ? t('studio.mix.song.full', { length })
          : t('studio.mix.song.proposal', { from: minutes(p.startSec), to: minutes(p.endSec), length })
        return phrase && suggestion.endsOnPhrase[n] === true && !whole ? `${said}, ${t('studio.mix.song.endsOnPhrase')}` : said
      })
      .join('; ')
  const control = (toggle: Toggle, text: string, set: readonly number[]) => (
    <label className="flex min-h-11 cursor-pointer items-center gap-2 text-[0.9375rem]">
      <input
        type="checkbox"
        checked={set.includes(position)}
        disabled={disabled}
        onChange={(event) => {
          onToggle(toggle, event.target.checked)
        }}
      />
      <span>
        {text}
        <span className="sr-only"> {label}</span>
      </span>
    </label>
  )
  const result = windows?.filter((w) => w.position === position) ?? []
  return (
    <li className="flex flex-col gap-1 rounded-md border border-line bg-surface p-3" data-testid="mix-song">
      <label className="flex min-h-11 cursor-pointer items-start gap-3">
        <input
          type="checkbox"
          className="mt-1"
          checked={selected}
          disabled={disabled}
          aria-label={t('studio.mix.select', { song: label })}
          onChange={(event) => {
            onSelect(event.target.checked)
          }}
        />
        <span className="break-words font-medium text-text">
          {position + 1}. {label}
        </span>
      </label>
      <p className="m-0 text-[0.9375rem] text-muted" data-testid="mix-proposal">
        {t('studio.mix.song.suggests', { plays: plays(suggestion.pieces, true) })}
      </p>
      {suggestion.fullOnly !== null ? (
        <p className="m-0 text-[0.9375rem] text-text" data-testid="mix-full-only">
          {t('studio.mix.fullOnly', { reason: t(FULL_ONLY[suggestion.fullOnly]) })}
        </p>
      ) : null}
      <div className="flex flex-wrap gap-x-5">
        {state.trim && suggestion.fullOnly === null ? control('keepWhole', t('studio.mix.song.keepWhole'), state.keepWhole) : null}
        {state.blend ? control('hardIn', t('studio.mix.song.hardCutIn'), state.hardIn) : null}
        {state.blend ? control('hardOut', t('studio.mix.song.hardCutOut'), state.hardOut) : null}
      </div>
      {isRepeated(suggestion) && state.trim ? (
        <p className="m-0 text-[0.9375rem] text-muted" data-testid="mix-plays-once">
          {t('studio.mix.song.playsOnce')}
        </p>
      ) : null}
      {result.length > 0 ? (
        <p className="m-0 text-[0.9375rem] text-text" data-testid="mix-result">
          {t('studio.mix.song.result', { plays: plays(result, false) })}
        </p>
      ) : null}
    </li>
  )
}
