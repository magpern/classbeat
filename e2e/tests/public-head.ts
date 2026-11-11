import { expect, type Page } from '@playwright/test'
import { PUBLISHED } from './public-paths'

// The head of a page as the tests compare it: parsed from the served HTML (what a crawler that does
// not run JavaScript sees) and read from the live document. Nothing else is looked at.

export interface Head {
  titles: string[]
  descriptions: string[]
  canonical: string[]
  alternates: string[]
  preview: string[]
  noindex: number
}

const ENTITIES: Record<string, string> = { amp: '&', lt: '<', gt: '>', quot: '"', '#39': "'", '#x27': "'" }
const decode = (text: string) => text.replace(/&(amp|lt|gt|quot|#39|#x27);/g, (_all, name: string) => ENTITIES[name] ?? '')

/** What the server sent, from the raw response. */
export function headOfHtml(html: string): Head {
  const head = html.slice(0, html.indexOf('</head>'))
  const tags = head.match(/<(?:meta|link)\b[^>]*>/g) ?? []
  const attrs = (tag: string) =>
    Object.fromEntries(
      [...tag.matchAll(/\b([a-z:-]+)="([^"]*)"/g)].map((m) => [m[1], decode(m[2] ?? '')] as const),
    ) as Record<string, string | undefined>
  const metas = tags.filter((t) => t.startsWith('<meta')).map(attrs)
  const links = tags.filter((t) => t.startsWith('<link')).map(attrs)
  return {
    titles: [...head.matchAll(/<title>([^<]*)<\/title>/g)].map((m) => decode(m[1] ?? '')),
    descriptions: metas.filter((m) => m.name === 'description').map((m) => m.content ?? ''),
    canonical: links.filter((l) => l.rel === 'canonical').map((l) => l.href ?? ''),
    alternates: links
      .filter((l) => l.rel === 'alternate' && l.hreflang !== undefined)
      .map((l) => `${l.hreflang ?? ''}=${l.href ?? ''}`)
      .sort(),
    preview: metas
      .map((m) => [m.property ?? m.name ?? '', m.content ?? ''] as const)
      .filter(([key]) => key.startsWith('og:') || key.startsWith('twitter:'))
      .map(([key, content]) => `${key}=${content}`)
      .sort(),
    noindex: metas.filter((m) => m.name === 'robots' && m.content === 'noindex').length,
  }
}

/** What the browser has now, after any takeover or navigation. */
export async function liveHead(page: Page): Promise<Head> {
  return page.evaluate(() => {
    const all = (selector: string) => [...document.head.querySelectorAll(selector)]
    const attr = (el: Element, name: string) => el.getAttribute(name) ?? ''
    return {
      titles: all('title').map((el) => el.textContent),
      descriptions: all('meta[name="description"]').map((el) => attr(el, 'content')),
      canonical: all('link[rel="canonical"]').map((el) => attr(el, 'href')),
      alternates: all('link[rel="alternate"][hreflang]')
        .map((el) => `${attr(el, 'hreflang')}=${attr(el, 'href')}`)
        .sort(),
      preview: all('meta[property^="og:"], meta[name^="twitter:"]')
        .map((el) => `${el.getAttribute('property') ?? attr(el, 'name')}=${attr(el, 'content')}`)
        .sort(),
      noindex: all('meta[name="robots"][content="noindex"]').length,
    }
  })
}

/** The neutral path of a prefixed URL: `/sv/privacy` is `/privacy`, `/sv` is "". */
export const neutralOf = (pathname: string) => pathname.replace(/^\/[a-z]{2}(?=\/|$)/, '').replace(/\/$/, '')

/**
 * The live head equals a raw GET of the same URL, each tag is there once, and `noindex` is on exactly
 * the pages that are not published (draft and app pages).
 */
export async function expectHeadIsServers(page: Page): Promise<void> {
  const url = new URL(page.url())
  const served = headOfHtml(await (await page.request.get(url.pathname + url.search)).text())
  const live = await liveHead(page)
  expect(live, url.pathname).toEqual(served)

  const published = PUBLISHED.includes(neutralOf(url.pathname))
  expect(served.titles, `${url.pathname}: one title`).toHaveLength(1)
  expect(served.descriptions, `${url.pathname}: one description`).toHaveLength(1)
  expect(served.canonical, `${url.pathname}: canonical`).toHaveLength(published ? 1 : 0)
  expect(served.noindex, `${url.pathname}: noindex`).toBe(published ? 0 : 1)
  expect(served.alternates.length > 0, `${url.pathname}: hreflang`).toBe(published)
  expect(served.preview.some((entry) => entry.startsWith('og:title=')), `${url.pathname}: og:title`).toBe(published)
}
