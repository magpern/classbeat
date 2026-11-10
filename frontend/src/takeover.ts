import { initialLocale, setActiveLocale } from './i18n/locales'
import { loadCatalogue } from './i18n/t'

/**
 * Client takeover of a prerendered page (ADR 0030 section 5). When the server filled `#root`, the
 * language's catalogue is loaded before the first render, so sv and da never flash English; the
 * provider then starts in that language. An empty `#root` (every app page) waits for nothing. A
 * failed load leaves the page in English, as it does everywhere else.
 */
export async function awaitCatalogue(root: Element, pathname: string): Promise<void> {
  if (root.firstElementChild === null) return
  const locale = initialLocale(pathname)
  if (await loadCatalogue(locale)) setActiveLocale(locale)
}
