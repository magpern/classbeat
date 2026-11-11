import paths from './publicPaths.json'

// The one list of public pages (ADR 0030 section 2). "" is the landing page, the rest are
// language-neutral paths without the language prefix. `published` pages are indexable in every
// language; `draft` pages are prerendered and taken over the same way but stay `noindex`.
export const PUBLISHED_PATHS: readonly string[] = paths.published
export const DRAFT_PATHS: readonly string[] = paths.draft

/** Every path the build prerenders, published first. */
export const LISTED_PATHS: readonly string[] = [...PUBLISHED_PATHS, ...DRAFT_PATHS]

/** True when the path is indexable in every language, so a link to it may be shown (ADR 0030 section 2). */
export function isPublished(path: string, published: readonly string[] = PUBLISHED_PATHS): boolean {
  return published.includes(path)
}
