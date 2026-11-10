import { DEFAULT_LOCALE } from '../i18n/locales'

// Pure checks on a prerendered page body (ADR 0030 section 1). Each returns the problems it
// found, empty when the page is fine, so the build can list them all at once.

/** Exactly one `<h1>`: a page with none or several has no single subject for a search engine. */
export function checkHeading(body: string): string[] {
  const count = (body.match(/<h1[\s>]/g) ?? []).length
  return count === 1 ? [] : [`expected exactly one <h1>, found ${String(count)}`]
}

/** No `style=` on any element: `style-src 'self'` refuses it in served HTML (ADR 0013). */
export function checkNoInlineStyle(body: string): string[] {
  return /<[^>]*\sstyle\s*=/i.test(body) ? ['an element has a style attribute (the CSP refuses it)'] : []
}

/** The body with its language prefix swapped for `en`, so a translated link does not hide an untranslated text. */
export function neutralBody(body: string, lang: string): string {
  return body.replace(new RegExp(`="/${lang}(?=[/"?#])`, 'g'), `="/${DEFAULT_LOCALE}`)
}

/** A published sv/da body that equals the English one was never translated. */
export function checkTranslated(lang: string, body: string, english: string): string[] {
  if (lang === 'en') return []
  return neutralBody(body, lang) === english
    ? [`the ${lang} body equals the English body`]
    : []
}
