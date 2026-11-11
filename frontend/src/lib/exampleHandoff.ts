import { EXAMPLE_SLUGS, type ExampleSlug } from '../publicSite/examples'

// The hand-off from an example page to the designer (PP6, ADR 0030). The link carries a SLUG from
// the fixed list of examples, never prompt text: the words come from the catalogue in the page's own
// language. Anything else in the query is dropped, so a crafted link cannot pre-fill arbitrary text
// or add a value to `return_to`. The slug is also kept in this browser for 24 hours, so it survives
// what `return_to` cannot carry: e-mail sign-up (the confirmation link opens a new session) and
// connecting MOWL on the account page. It is removed as soon as the start screen uses it.

const KEY = 'classbeat.example'
export const EXAMPLE_TTL_MS = 24 * 60 * 60 * 1000

/** The slug if it names an example, else null: the only check between a URL and the prompt. */
export function validExample(value: string | null): ExampleSlug | null {
  return EXAMPLE_SLUGS.find((slug) => slug === value) ?? null
}

/** `return_to` for a signed-out visitor: the path, plus the validated `example` and nothing else from the query. */
export function returnToWithExample(path: string, search: string): string {
  const slug = validExample(new URLSearchParams(search).get('example'))
  return slug === null ? path : `${path}?example=${slug}`
}

export function rememberExample(slug: ExampleSlug, now = Date.now()): void {
  try {
    window.localStorage.setItem(KEY, JSON.stringify({ slug, at: now }))
  } catch {
    // Without storage only the link itself carries the example.
  }
}

export function forgetExample(): void {
  try {
    window.localStorage.removeItem(KEY)
  } catch {
    // Nothing to remove.
  }
}

/** The kept slug, or null when there is none, it is older than 24 hours or it is not an example (it only reads: the caller removes an expired entry with `forgetExample`). */
export function recallExample(now = Date.now()): ExampleSlug | null {
  try {
    const raw = window.localStorage.getItem(KEY)
    if (raw === null) return null
    const kept = JSON.parse(raw) as { slug?: unknown; at?: unknown } | null
    const slug = typeof kept?.slug === 'string' ? validExample(kept.slug) : null
    const at = typeof kept?.at === 'number' ? kept.at : Number.NaN
    if (slug !== null && now - at >= 0 && now - at < EXAMPLE_TTL_MS) return slug
    return null
  } catch {
    return null
  }
}
