export type ThemeChoice = 'dark' | 'light' | 'system'

export const THEME_STORAGE_KEY = 'cb-theme'
export const DEFAULT_THEME: ThemeChoice = 'dark'
export const THEME_CHOICES: readonly { value: ThemeChoice; label: string }[] = [
  { value: 'dark', label: 'Dark' },
  { value: 'light', label: 'Light' },
  { value: 'system', label: 'Same as device' },
]

export function isThemeChoice(value: unknown): value is ThemeChoice {
  return value === 'dark' || value === 'light' || value === 'system'
}

/** The saved choice, or Dark when nothing is saved or storage is unavailable. */
export function readTheme(): ThemeChoice {
  try {
    const saved = window.localStorage.getItem(THEME_STORAGE_KEY)
    return isThemeChoice(saved) ? saved : DEFAULT_THEME
  } catch {
    return DEFAULT_THEME
  }
}

export function applyTheme(choice: ThemeChoice): void {
  document.documentElement.setAttribute('data-theme', choice)
}

/** Applies and keeps the choice; the page works without storage. */
export function setTheme(choice: ThemeChoice): void {
  applyTheme(choice)
  try {
    window.localStorage.setItem(THEME_STORAGE_KEY, choice)
  } catch {
    /* storage blocked: the choice lasts until the page closes */
  }
}
