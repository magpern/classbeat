import { expect, test } from '@playwright/test'
import { CODES, WORDS } from './lang-url-helpers'

// #719 (ADR 0027 section 6): what the server adds to the page for a language-prefixed URL: the
// `<html lang>`, and for the three public pages a canonical link and hreflang alternates for every
// enabled language plus x-default. They are read from the raw response (what a crawler that does
// not run JavaScript sees) and, for lang, again in the browser. The host in the links is
// `public_base_url`, which is not the test's own origin, so only paths are compared.

const PUBLIC = ['', '/privacy', '/terms']

interface Link {
  rel: string
  hreflang?: string
  href: string
}

function links(html: string): Link[] {
  const found: Link[] = []
  for (const tag of html.match(/<link\b[^>]*>/g) ?? []) {
    const attr = (name: string) => new RegExp(`\\b${name}="([^"]*)"`).exec(tag)?.[1]
    const rel = attr('rel')
    const href = attr('href')
    if (rel === 'canonical' || rel === 'alternate') found.push({ rel, hreflang: attr('hreflang'), href: href ?? '' })
  }
  return found
}

const pathOf = (href: string) => new URL(href).pathname

for (const code of CODES) {
  test.describe(WORDS[code].name, () => {
    for (const page of PUBLIC) {
      const path = `/${code}${page}`

      test(`${path}: lang, canonical and hreflang in the served page`, async ({ request }) => {
        const response = await request.get(path)
        expect(response.status()).toBe(200)
        expect(response.headers()['cache-control']).toBe('no-cache')
        const html = await response.text()

        expect(html).toMatch(new RegExp(`<html[^>]*\\blang="${code}"`))
        expect(html).not.toContain('name="robots" content="noindex"')

        const found = links(html)
        expect(found.filter((l) => l.rel === 'canonical').map((l) => pathOf(l.href))).toEqual([path])
        const alternates = Object.fromEntries(
          found.filter((l) => l.rel === 'alternate').map((l) => [l.hreflang, pathOf(l.href)]),
        )
        expect(alternates).toEqual({
          ...Object.fromEntries(CODES.map((c) => [c, `/${c}${page}`])),
          'x-default': `/en${page}`,
        })
      })

      test(`${path}: the browser shows the same lang and the links stay in the head`, async ({ page: tab }) => {
        await tab.goto(path)
        await expect(tab.locator('html')).toHaveAttribute('lang', code)
        await expect(tab.locator('head link[rel="canonical"]')).toHaveAttribute('href', new RegExp(`${path}$`))
        // every language and x-default
        await expect(tab.locator('head link[rel="alternate"][hreflang]')).toHaveCount(CODES.length + 1)
      })
    }
  })
}

test.describe('pages that are not for search', () => {
  test('the sign-in page has its lang and noindex, and no canonical', async ({ request }) => {
    const html = await (await request.get('/sv/signin')).text()
    expect(html).toMatch(/<html[^>]*\blang="sv"/)
    expect(html).toContain('name="robots" content="noindex"')
    expect(links(html)).toEqual([])
  })

  test('an unknown language is a 404 that still serves the app', async ({ page, request }) => {
    const response = await request.get('/fr/privacy')
    expect(response.status()).toBe(404)
    expect(await response.text()).toContain('<div id="root"')
    await page.goto('/fr/privacy')
    await expect(page.locator('html')).toHaveAttribute('lang', 'en')
  })
})

test.describe('sitemap and robots', () => {
  test('the sitemap lists the public pages in every language with their alternates', async ({ request }) => {
    const response = await request.get('/sitemap.xml')
    expect(response.status()).toBe(200)
    const xml = await response.text()
    const locs = [...xml.matchAll(/<loc>([^<]+)<\/loc>/g)].map((m) => pathOf(m[1]!))
    expect(locs.sort()).toEqual(
      CODES.flatMap((code) => PUBLIC.map((page) => `/${code}${page}`)).sort(),
    )
    expect(xml.match(/hreflang="x-default"/g)).toHaveLength(PUBLIC.length * CODES.length)
  })

  test('robots.txt blocks nothing and names the sitemap', async ({ request }) => {
    const text = await (await request.get('/robots.txt')).text()
    expect(text).toContain('Allow: /')
    expect(text).not.toMatch(/Disallow:\s*\S/)
    expect(text).toMatch(/^Sitemap: \S+\/sitemap\.xml$/m)
  })
})
