import manifest from './locales.json'

// The supported locales are the checked-in manifest, nothing else (ADR 0023 section 1). A locale is
// an allowlisted code: it never builds a path, an import specifier or a header (section 9).
export type Locale = string

export const LOCALES: readonly Locale[] = manifest
export const DEFAULT_LOCALE: Locale = 'en'
export const LOCALE_STORAGE_KEY = 'cb-locale'

export function isLocale(value: unknown): value is Locale {
  return typeof value === 'string' && LOCALES.includes(value)
}

/** `sv-SE` and `sv_se` as `sv`; anything else as given, lower-cased. */
function language(tag: string): string {
  return (tag.split(/[-_]/)[0] ?? '').toLowerCase()
}

/** The saved choice, or null when nothing valid is saved or storage is unavailable. */
export function readStoredLocale(): Locale | null {
  try {
    const saved = window.localStorage.getItem(LOCALE_STORAGE_KEY)
    return isLocale(saved) ? saved : null
  } catch {
    return null
  }
}

export function storeLocale(locale: Locale): void {
  try {
    window.localStorage.setItem(LOCALE_STORAGE_KEY, locale)
  } catch {
    /* storage blocked: the choice lasts until the page closes */
  }
}

/** The first browser language the manifest has, else English. */
export function fromLanguages(languages: readonly string[]): Locale {
  for (const tag of languages) {
    const code = language(tag)
    if (isLocale(code)) return code
  }
  return DEFAULT_LOCALE
}

/** Browser-side resolution (ADR 0023 section 2): `cb-locale`, then `navigator.languages`, then `en`. */
export function resolveBrowserLocale(): Locale {
  const stored = readStoredLocale()
  if (stored !== null) return stored
  try {
    return fromLanguages(window.navigator.languages)
  } catch {
    return DEFAULT_LOCALE
  }
}

// The locale the formatters and `t` use. The provider sets it; plain functions (no hook) read it.
let active: Locale = DEFAULT_LOCALE

export function getActiveLocale(): Locale {
  return active
}

export function setActiveLocale(locale: Locale): void {
  active = isLocale(locale) ? locale : DEFAULT_LOCALE
}
