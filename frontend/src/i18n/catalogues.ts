import type { Catalogue } from './en'
import type { Locale } from './locales'

// Non-English catalogues load through this static map, one dynamic import each, so each is its
// own chunk and no import specifier is ever built from a locale (ADR 0023 sections 3 and 9).
// `locales.json` lists the same locales; a test pins both lists together.
export type Loader = () => Promise<{ default: Catalogue }>

export const CATALOGUE_LOADERS: Readonly<Record<Locale, Loader>> = {
  sv: () => import('./sv'),
  da: () => import('./da'),
}
