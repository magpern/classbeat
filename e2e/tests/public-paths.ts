import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

// The one list of public pages (ADR 0030 section 2), read from the frontend's own file so the specs
// follow it. "" is the landing page; the rest are language-neutral paths.
const file = fileURLToPath(new URL('../../frontend/src/publicSite/publicPaths.json', import.meta.url))
const lists = JSON.parse(readFileSync(file, 'utf8')) as { published: string[]; draft: string[] }

/** Indexable in every language: canonical, hreflang, Open Graph, a sitemap entry. */
export const PUBLISHED: readonly string[] = lists.published
/** Prerendered and taken over like the others, but noindex and not in the sitemap. */
export const DRAFT: readonly string[] = lists.draft
export const LISTED: readonly string[] = [...PUBLISHED, ...DRAFT]
