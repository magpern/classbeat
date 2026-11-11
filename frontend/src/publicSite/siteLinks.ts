import type { CatalogueKey } from '../i18n/en'
import { isPublished, PUBLISHED_PATHS } from './publicPaths'

// The links to the new public pages (PP4, #893). A link renders only when its path is `published`
// (ADR 0030 section 2), so a draft page is routed and prerendered but not linked. PP8 (#897)
// published every path, so all three links show; it added no string (Examples reuses `examples.title`).

export interface SiteLink {
  path: string
  label: CatalogueKey
}

export const SITE_LINKS: readonly SiteLink[] = [
  { path: '/how-it-works', label: 'landing.how.title' },
  { path: '/examples', label: 'examples.title' },
  { path: '/features', label: 'pub.nav.features' },
]

export function visibleSiteLinks(published: readonly string[] = PUBLISHED_PATHS): SiteLink[] {
  return SITE_LINKS.filter((link) => isPublished(link.path, published))
}
