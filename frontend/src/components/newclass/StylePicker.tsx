import { ChipButton } from '../ui/chip'
import { TextInput } from './inputs'

export const MAX_STYLE = 60
const STYLES = ['80s', '90s', '2000s', 'Pop', 'Rock', 'Dance', 'Hip hop', 'Latin'] as const

function words(style: string): string[] {
  return style
    .split(',')
    .map((w) => w.trim())
    .filter((w) => w !== '')
}

/** Style chips for era and genre, over the free-text "Style" field they fill in. */
export function StylePicker({ value, onChange }: { value: string; onChange: (style: string) => void }) {
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
      <div role="group" aria-label="Style suggestions" className="flex flex-wrap gap-2">
        {STYLES.map((chip) => {
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
              {chip}
            </ChipButton>
          )
        })}
      </div>
      <TextInput
        label="Style"
        value={value}
        maxLength={MAX_STYLE}
        hint="For example: hills, intervals, endurance."
        onChange={(e) => {
          onChange(e.target.value)
        }}
      />
    </div>
  )
}
