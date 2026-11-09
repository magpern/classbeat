import { useState } from 'react'
import { useT } from '../i18n/I18n'
import { SegmentedControl } from '../components/ui/segmented-control'
import { THEME_CHOICES, readTheme, setTheme, type ThemeChoice } from './theme'

/** Dark, Light or Same as device. Mounted in Account, Appearance (UI6). */
export function ThemeSwitch() {
  const t = useT()
  const [choice, setChoice] = useState<ThemeChoice>(readTheme)
  return (
    <SegmentedControl
      aria-label={t('theme.label')}
      value={choice}
      options={THEME_CHOICES.map((c) => ({ value: c.value, label: t(c.label) }))}
      onValueChange={(value) => {
        setChoice(value)
        setTheme(value)
      }}
    />
  )
}
