import { isLocale, localeFromPath, type Locale } from '../i18n/locales'

// Pure path helpers for the language-prefixed URLs of ADR 0027. Everything here works on the
// PATHNAME only: a query or fragment is cut off before any prefix check, so `/sv?x=1` and `/sv#y`
// are `/sv`, never `/sv/sv?x=1`. A language is an allowlisted code (`isLocale`), never a value from a URL.

/** Areas that never carry a prefix: the API, the provider callbacks, and the English-only admin. */
const NEVER_PREFIXED = /^\/(admin|api|oauth)(\/|$)/

export interface SplitPath {
  /** The language the URL names, or null for an unprefixed URL. */
  lang: Locale | null
  /** The path without the prefix and without a trailing slash; `/` for the home page. */
  path: string
}

/** Splits a pathname into the language prefix and the neutral path. */
export function splitPath(pathname: string): SplitPath {
  const trimmed = pathname.replace(/\/+$/, '') || '/'
  const lang = localeFromPath(trimmed)
  if (lang === null) return { lang: null, path: trimmed }
  return { lang, path: trimmed.slice(lang.length + 1) || '/' }
}

export function isAdminPath(path: string): boolean {
  return /^\/admin(\/|$)/.test(path)
}

/** A first segment of two lowercase letters that the manifest does not have (`/fr/privacy`, ADR 0027 section 5). */
export function hasUnknownLanguage(pathname: string): boolean {
  const first = /^\/([a-z]{2})(?:\/|$)/.exec(pathname)?.[1]
  return first !== undefined && !isLocale(first)
}

/** `/<lang><path>` for a neutral path (`/` becomes `/<lang>`). */
export function withLanguage(lang: Locale, path: string): string {
  return path === '/' ? `/${lang}` : `/${lang}${path}`
}

function cutAtQuery(to: string): [string, string] {
  const cut = to.search(/[?#]/)
  return cut === -1 ? [to, ''] : [to.slice(0, cut), to.slice(cut)]
}

/**
 * Adds the prefix to a neutral target (`/classes?x=1#y`). A target that already starts with an
 * allowlisted `/<code>/` or is `/<code>` is left alone, so a mixed value cannot become `/sv/sv/...`;
 * so is `/admin`, `/api` and `/oauth`.
 */
export function localize(to: string, lang: Locale): string {
  const [pathname, rest] = cutAtQuery(to)
  const safe = pathname.startsWith('/') ? pathname : `/${pathname}`
  if (NEVER_PREFIXED.test(safe) || localeFromPath(safe) !== null) return safe + rest
  return withLanguage(lang, safe) + rest
}

/** Removes a language prefix from a target, keeping its query and fragment: `return_to` is language-neutral. */
export function neutralize(to: string): string {
  const [pathname, rest] = cutAtQuery(to)
  const { lang, path } = splitPath(pathname)
  const neutral = path + rest
  // `/sv//host` must not become `//host`, which a browser reads as another origin: keep it as it was.
  return lang === null || neutral.startsWith('//') ? to : neutral
}

/**
 * Where an unprefixed URL goes (ADR 0027 section 3): always `/<lang>/...` on the current origin,
 * built from an allowlisted code plus the current pathname, search and fragment, never from a
 * query value. a path of two slashes and a host becomes `/sv` plus that path: a path on this origin.
 */
export function prefixedTarget(lang: Locale, pathname: string, search: string, hash: string): string {
  return `${withLanguage(lang, splitPath(pathname).path)}${search}${hash}`
}
