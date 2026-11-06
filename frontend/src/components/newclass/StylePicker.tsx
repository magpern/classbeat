import { useT } from '../../i18n/I18n'
import type { CatalogueKey } from '../../i18n/en'
import { ChipButton } from '../ui/chip'
import { TextInput } from './inputs'

export const MAX_STYLE = 60
// The chip shows a translated label but adds its English `value` to the style, so the brief the
// designer reads is the same in every language.
const STYLES: readonly { value: string; labelKey: CatalogueKey }[] = [
  { value: '80s', labelKey: 'newclass.style.80s' },
  { value: '90s', labelKey: 'newclass.style.90s' },
  { value: '2000s', labelKey: 'newclass.style.2000s' },
  { value: 'Pop', labelKey: 'newclass.style.pop' },
  { value: 'Rock', labelKey: 'newclass.style.rock' },
  { value: 'Dance', labelKey: 'newclass.style.dance' },
  { value: 'Hip hop', labelKey: 'newclass.style.hiphop' },
  { value: 'Latin', labelKey: 'newclass.style.latin' },
]

function words(style: string): string[] {
  return style
    .split(',')
    .map((w) => w.trim())
    .filter((w) => w !== '')
}

/** Style chips for era and genre, over the free-text "Style" field they fill in. */
export function StylePicker({ value, onChange }: { value: string; onChange: (style: string) => void }) {
  const t = useT()
  const chosen = words(value).map((w) => w.toLowerCase())
  const toggle = (chip: string) => {
    const current = words(value)
    const next = chosen.includes(chip.toLowerCase())
      ? current.filter((w) => w.toLowerCase() !== chip.toLowerCase())
      : [...current, chip]
    onChange(next.join(', '))
  }
  return (
    <div className="flex flex-col gap-3">
      <div role="group" aria-label={t('newclass.style.suggestions')} className="flex flex-wrap gap-2">
        {STYLES.map(({ value: chip, labelKey }) => {
          const on = chosen.includes(chip.toLowerCase())
          return (
            <ChipButton
              key={chip}
              pressed={on}
              disabled={!on && [...words(value), chip].join(', ').length > MAX_STYLE}
              onClick={() => {
                toggle(chip)
              }}
            >
              {t(labelKey)}
            </ChipButton>
          )
        })}
      </div>
      <TextInput
        label={t('newclass.style.label')}
        value={value}
        maxLength={MAX_STYLE}
        hint={t('newclass.style.hint')}
        onChange={(e) => {
          onChange(e.target.value)
        }}
      />
    </div>
  )
}
