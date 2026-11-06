import { useState } from 'react'
import { useT } from '../i18n/I18n'
import { ToggleGroup, ToggleGroupItem } from '../components/ui/toggle-group'
import { THEME_CHOICES, isThemeChoice, readTheme, setTheme, type ThemeChoice } from './theme'

/** Dark, Light or Same as device. Mounted in Account, Appearance (UI6). */
export function ThemeSwitch() {
  const t = useT()
  const [choice, setChoice] = useState<ThemeChoice>(readTheme)
  return (
    <ToggleGroup
      type="single"
      aria-label={t('theme.label')}
      value={choice}
      onValueChange={(value) => {
        // Radix reports an empty string when the active item is pressed again; keep the choice.
        if (!isThemeChoice(value)) return
        setChoice(value)
        setTheme(value)
      }}
    >
      {THEME_CHOICES.map((c) => (
        <ToggleGroupItem key={c.value} value={c.value}>
          {t(c.label)}
        </ToggleGroupItem>
      ))}
    </ToggleGroup>
  )
}
