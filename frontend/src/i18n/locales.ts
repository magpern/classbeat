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
  if (!isLocale(locale)) return
  try {
    window.localStorage.setItem(LOCALE_STORAGE_KEY, locale)
  } catch {
    /* storage blocked: the choice lasts until the page closes */
  }
}

export function clearStoredLocale(): void {
  try {
    window.localStorage.removeItem(LOCALE_STORAGE_KEY)
  } catch {
    /* storage blocked: nothing to clear */
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

/**
 * The language of a URL path (ADR 0027 section 1): its first segment when that is a manifest code,
 * else null. The path only: a query or fragment never reaches this.
 */
export function localeFromPath(pathname: string): Locale | null {
  const match = /^\/([^/]+)(?:\/|$)/.exec(pathname)
  const code = match?.[1] ?? null
  return code !== null && isLocale(code) ? code : null
}

/**
 * The language to use when the URL names none (ADR 0027 section 3): the account's, the saved
 * choice, the browser's, then English; the first one the server offers. An empty `offered` means
 * the server has not said (session not loaded or failed): any supported language will do.
 */
export function bestLocale(offered: readonly string[], account: string | null): Locale {
  const allowed = (code: Locale) => offered.length === 0 || offered.includes(code)
  const first = (code: string | null): Locale | null =>
    code !== null && isLocale(code) && allowed(code) ? code : null
  const fromAccount = first(account)
  if (fromAccount !== null) return fromAccount
  const stored = first(readStoredLocale())
  if (stored !== null) return stored
  try {
    for (const tag of window.navigator.languages) {
      const found = first(language(tag))
      if (found !== null) return found
    }
  } catch {
    /* navigator blocked: stay on the default */
  }
  // English unless the server does not offer it; then the first offered code the manifest has.
  return allowed(DEFAULT_LOCALE) ? DEFAULT_LOCALE : (LOCALES.find(allowed) ?? DEFAULT_LOCALE)
}

// The locale the formatters and `t` use. The provider sets it; plain functions (no hook) read it.
let active: Locale = DEFAULT_LOCALE

export function getActiveLocale(): Locale {
  return active
}

export function setActiveLocale(locale: Locale): void {
  active = isLocale(locale) ? locale : DEFAULT_LOCALE
}
