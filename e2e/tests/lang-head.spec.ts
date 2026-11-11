import { expect, test } from '@playwright/test'
import { CODES, WORDS } from './lang-url-helpers'
import { DRAFT, PUBLISHED } from './public-paths'

// #719 (ADR 0027 section 6): what the server adds to the page for a language-prefixed URL: the
// `<html lang>`, and for the published public pages a canonical link and hreflang alternates for every
// enabled language plus x-default. They are read from the raw response (what a crawler that does
// not run JavaScript sees) and, for lang, again in the browser. The host in the links is
// `public_base_url`, which is not the test's own origin, so only paths are compared.

const PUBLIC = PUBLISHED

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

      test(`${path}: the raw HTML holds one heading in the language of the page (PP8)`, async ({ request }) => {
        const html = await (await request.get(path)).text()
        expect(html.match(/<h1\b/g)).toHaveLength(1)
        expect(html.match(/<title>/g)).toHaveLength(1)
        const h1 = (text: string) => /<h1\b[^>]*>([\s\S]*?)<\/h1>/.exec(text)?.[1]?.replace(/<[^>]*>/g, '').trim() ?? ''
        expect(h1(html)).not.toBe('')
        if (code !== 'en') {
          const english = await (await request.get(`/en${page}`)).text()
          expect(h1(html), 'the heading differs from the English one').not.toBe(h1(english))
        }
      })

      test(`${path}: the link preview names the image, its size and the large card (#854)`, async ({ request }) => {
        const html = await (await request.get(path)).text()
        const og = (key: string) => new RegExp(`<meta (?:property|name)="${key}" content="([^"]*)">`).exec(html)?.[1]
        expect(pathOf(og('og:image') ?? '')).toBe('/img/og-share.jpg')
        expect(og('og:image:width')).toBe('1200')
        expect(og('og:image:height')).toBe('630')
        expect(og('twitter:card')).toBe('summary_large_image')
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
    expect(html).not.toContain('og:image')
  })

  for (const code of CODES) {
    for (const page of ['/signin', '/signup', '/classes']) {
      test(`/${code}${page}: an app page stays noindex, with no canonical or hreflang (PP8)`, async ({ request }) => {
        const html = await (await request.get(`/${code}${page}`)).text()
        expect(html).toContain('name="robots" content="noindex"')
        expect(links(html)).toEqual([])
        expect(html).not.toContain('og:title')
      })
    }
  }

  // PP8 published every path, so `draft` is empty; the loop keeps the checks for the next draft page.
  for (const page of DRAFT) {
    test(`/sv${page}: a draft page is prerendered but noindex, with no canonical, hreflang or preview`, async ({ request }) => {
      const html = await (await request.get(`/sv${page}`)).text()
      expect(html).toMatch(/<html[^>]*\blang="sv"/)
      expect(html).toContain('name="robots" content="noindex"')
      expect(links(html)).toEqual([])
      expect(html).not.toContain('og:title')
      expect(html.match(/<h1\b/g)).toHaveLength(1)
    })

    test(`${page} is not in the sitemap`, async ({ request }) => {
      const xml = await (await request.get('/sitemap.xml')).text()
      expect(xml).not.toContain(`${page}<`)
    })
  }

  test('the link-preview image and a picture of the page are served with their types (#854)', async ({ request }) => {
    const preview = await request.get('/img/og-share.jpg')
    expect(preview.status()).toBe(200)
    expect(preview.headers()['content-type']).toBe('image/jpeg')
    const hero = await request.get('/img/hero-light-1600.avif')
    expect(hero.status()).toBe(200)
    expect(hero.headers()['content-type']).toBe('image/avif')
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
