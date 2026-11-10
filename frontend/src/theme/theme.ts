import type { CatalogueKey } from '../i18n/en'

export type ThemeChoice = 'dark' | 'light' | 'system'

export const THEME_STORAGE_KEY = 'cb-theme'
export const DEFAULT_THEME: ThemeChoice = 'dark'
export const THEME_CHOICES: readonly { value: ThemeChoice; label: CatalogueKey }[] = [
  { value: 'dark', label: 'theme.dark' },
  { value: 'light', label: 'theme.light' },
  { value: 'system', label: 'theme.system' },
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

/** The theme the page shows: `system` is resolved by the device's light or dark preference. */
export function resolvedTheme(): 'dark' | 'light' {
  // No document in the build-time prerender: the default theme, as before the saved choice is applied.
  if (typeof document === 'undefined') return DEFAULT_THEME === 'system' ? 'dark' : DEFAULT_THEME
  const attribute = document.documentElement.getAttribute('data-theme')
  const choice = isThemeChoice(attribute) ? attribute : DEFAULT_THEME
  if (choice !== 'system') return choice
  return lightPreferred()?.matches === true ? 'light' : 'dark'
}

/** The device's light preference, or null where the browser has no media queries. */
export function lightPreferred(): MediaQueryList | null {
  return typeof window !== 'undefined' && typeof window.matchMedia === 'function' ? window.matchMedia('(prefers-color-scheme: light)') : null
}
