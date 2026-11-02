import { useId } from 'react'
import type { SwedebeatMix } from '../../api/swedebeatMixes'
import { lengthLabel } from '../../lib/classCards'
import { Chip } from '../ui/chip'
import { initials } from './PlaylistTiles'

/**
 * The user's Swedebeat mixes as a tile grid (ADR 0016 section 3, ADR 0013 section 9). Real radio
 * buttons in one group; an "Analysed before" badge when classbeat already holds a map for the mix.
 */
export function MixTiles({
  mixes,
  value,
  onChange,
}: {
  mixes: SwedebeatMix[]
  value: string
  onChange: (mixId: string) => void
}) {
  const group = useId()
  if (mixes.length === 0) {
    return (
      <p className="m-0 text-[0.9375rem] text-muted">
        MOWL lists no Swedebeat mixes for your account yet. Create a mix in the Swedebeat app, let it
        sync to Intelligent Cycling, then reload this page.
      </p>
    )
  }
  return (
    <div className="flex flex-col gap-2">
      <span id={`${group}-label`} className="text-[0.9375rem] font-medium text-text">
        Swedebeat mix
      </span>
      <div
        role="radiogroup"
        aria-labelledby={`${group}-label`}
        aria-describedby={`${group}-hint`}
        className="grid grid-cols-2 gap-3 sm:grid-cols-3"
      >
        {mixes.map((mix) => {
          const name = mix.name || 'Untitled mix'
          const length =
            mix.durationSeconds === null ? null : lengthLabel(mix.durationSeconds)
          return (
            <label
              key={mix.mixId}
              className="relative flex min-h-11 cursor-pointer flex-col gap-2 rounded-md border border-line bg-raised p-3 hover:bg-line has-checked:border-brand has-checked:bg-surface has-checked:outline-2 has-checked:outline-brand has-focus-visible:outline-2 has-focus-visible:outline-offset-2 has-focus-visible:outline-brand"
            >
              <input
                type="radio"
                name={`${group}-mix`}
                value={mix.mixId}
                checked={value === mix.mixId}
                onChange={() => {
                  onChange(mix.mixId)
                }}
                className="sr-only"
              />
              <span
                aria-hidden="true"
                className="flex aspect-square w-full items-center justify-center rounded-sm bg-surface font-display text-2xl font-extrabold text-muted"
              >
                {initials(name)}
              </span>
              <span className="flex flex-col gap-1 break-words text-[0.9375rem] font-medium text-text">
                <span>{name}</span>
                {length !== null ? <span className="text-[0.8125rem] font-normal text-muted">{length}</span> : null}
              </span>
              {mix.analysed ? (
                <Chip tone="ok" className="w-fit">
                  Analysed before
                </Chip>
              ) : null}
            </label>
          )
        })}
      </div>
      <p id={`${group}-hint`} className="m-0 text-[0.8125rem] text-muted">
        classbeat adds this mix to your MOWL music so it can analyse the audio and read the songs. The
        first analysis can take about a minute.
      </p>
    </div>
  )
}
