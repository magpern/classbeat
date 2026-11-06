import { CATALOGUE_LOADERS, type Loader } from './catalogues'
import { en, type Catalogue, type CatalogueKey, type Message } from './en'
import { DEFAULT_LOCALE, getActiveLocale, type Locale } from './locales'

export type Params = Readonly<Record<string, string | number>>

const loaded = new Map<Locale, Catalogue>()

/** The catalogue of a locale once loaded (English is always there). Own keys only. */
function catalogueOf(locale: Locale): Catalogue {
  return loaded.get(locale) ?? {}
}

/**
 * Loads a locale's catalogue (a no-op for English). The loader map is the allowlist: a locale it does
 * not name loads nothing. A failed load leaves the locale on English text.
 * `loaders` is for tests; the app uses the static map.
 */
export async function loadCatalogue(
  locale: Locale,
  loaders: Readonly<Record<Locale, Loader>> = CATALOGUE_LOADERS,
): Promise<boolean> {
  if (locale === DEFAULT_LOCALE) return true
  if (loaded.has(locale)) return true
  const load = Object.hasOwn(loaders, locale) ? loaders[locale] : undefined
  if (load === undefined) return false
  try {
    loaded.set(locale, (await load()).default)
    return true
  } catch {
    return false
  }
}

/** Forgets loaded catalogues (tests). */
export function resetCatalogues(): void {
  loaded.clear()
}

/** The plural form for `count` in `locale`, falling back to `other`. */
export function plural(locale: Locale, count: number, forms: Message): string {
  if (typeof forms === 'string') return forms
  const category = new Intl.PluralRules(locale).select(count)
  return forms[category] ?? forms.other
}

const PLACEHOLDER = /\{([A-Za-z_][A-Za-z0-9_]*)\}/g

function fill(text: string, params: Params | undefined): string {
  if (params === undefined) return text
  return text.replace(PLACEHOLDER, (whole, name: string) =>
    Object.hasOwn(params, name) ? String(params[name]) : whole,
  )
}

function message(locale: Locale, key: CatalogueKey): { text: Message; locale: Locale } {
  const own = catalogueOf(locale)
  if (Object.hasOwn(own, key)) {
    const found = own[key]
    if (found !== undefined) return { text: found, locale }
  }
  return { text: en[key], locale: DEFAULT_LOCALE }
}

/** The text of `key` in `locale`, English when the key is missing there; `{name}` placeholders filled. */
export function translate(locale: Locale, key: CatalogueKey, params?: Params): string {
  const { text, locale: from } = message(locale, key)
  const chosen =
    typeof text === 'string' ? text : plural(from, typeof params?.count === 'number' ? params.count : 0, text)
  return fill(chosen, params)
}

export type TFunction = (key: CatalogueKey, params?: Params) => string

/** `t` for the active locale, for code that is not a component. */
export const t: TFunction = (key, params) => translate(getActiveLocale(), key, params)
