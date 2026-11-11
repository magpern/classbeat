import type { CatalogueKey } from '../i18n/en'
import { EXAMPLE_SLUGS, exampleMetaKeys } from './examples'

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
  '/how-it-works': { title: 'meta.howItWorks.title', description: 'meta.howItWorks.description' },
  '/features': { title: 'meta.features.title', description: 'meta.features.description' },
  '/pricing': { title: 'meta.pricing.title', description: 'meta.pricing.description' },
  '/help': { title: 'meta.help.title', description: 'meta.help.description' },
  '/examples': { title: 'meta.examples.title', description: 'meta.examples.description' },
  ...Object.fromEntries(EXAMPLE_SLUGS.map((slug) => [`/examples/${slug}`, exampleMetaKeys(slug)])),
}

export function metaKeys(path: string): MetaKeys {
  const found = Object.hasOwn(META, path) ? META[path] : undefined
  if (found === undefined) throw new Error(`no title and description keys for the public path "${path}"`)
  return found
}
