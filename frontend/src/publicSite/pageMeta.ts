import type { CatalogueKey } from '../i18n/en'

export interface MetaKeys {
  title: CatalogueKey
  description: CatalogueKey
}

// The <title> and the description of each public page are catalogue keys, so the translation
// guard covers them (ADR 0030 section 3). A listed path with no entry here fails the build.
const META: Readonly<Record<string, MetaKeys>> = {
  '': { title: 'meta.landing.title', description: 'meta.landing.description' },
  '/privacy': { title: 'meta.privacy.title', description: 'meta.privacy.description' },
  '/terms': { title: 'meta.terms.title', description: 'meta.terms.description' },
}

export function metaKeys(path: string): MetaKeys {
  const found = Object.hasOwn(META, path) ? META[path] : undefined
  if (found === undefined) throw new Error(`no title and description keys for the public path "${path}"`)
  return found
}
