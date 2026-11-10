import { LOCALES } from '../i18n/locales'
import { LISTED_PATHS, PUBLISHED_PATHS } from '../publicSite/publicPaths'
import { checkHeading, checkNoInlineStyle, checkTranslated } from '../publicSite/prerenderChecks'
import { renderPage, type PrerenderedPage } from './renderPage'

/** `prerender.json`: language, then path ("" is the landing page), then the page. */
export type Prerendered = Record<string, Record<string, PrerenderedPage>>

/** The prerender makes no network call: a page that fetches fails the build instead of waiting. */
async function withoutFetch<T>(work: () => Promise<T>): Promise<T> {
  const original = globalThis.fetch
  globalThis.fetch = () => {
    throw new Error('fetch was called during the prerender')
  }
  try {
    return await work()
  } finally {
    globalThis.fetch = original
  }
}

function label(lang: string, path: string): string {
  return `${lang}${path === '' ? '/' : path}`
}

/** Every listed path in every language, or an error that lists each problem found. */
export async function prerenderAll(): Promise<Prerendered> {
  const out: Prerendered = {}
  const problems: string[] = []
  await withoutFetch(async () => {
    for (const lang of LOCALES) {
      const pages: Record<string, PrerenderedPage> = {}
      out[lang] = pages
      for (const path of LISTED_PATHS) {
        try {
          pages[path] = await renderPage(lang, path)
        } catch (error) {
          problems.push(`${label(lang, path)}: render failed: ${error instanceof Error ? error.message : String(error)}`)
        }
      }
    }
  })
  for (const [lang, pages] of Object.entries(out)) {
    for (const [path, page] of Object.entries(pages)) {
      const found = [...checkHeading(page.body), ...checkNoInlineStyle(page.body)]
      const english = out.en?.[path]
      // Only published pages must be translated: a draft may stay English until it is published.
      if (english !== undefined && PUBLISHED_PATHS.includes(path)) {
        found.push(...checkTranslated(lang, page.body, english.body))
      }
      problems.push(...found.map((problem) => `${label(lang, path)}: ${problem}`))
    }
  }
  if (problems.length > 0) throw new Error(`prerender failed:\n${problems.join('\n')}`)
  return out
}
