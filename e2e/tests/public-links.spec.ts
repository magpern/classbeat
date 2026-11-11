import { expect, test } from '@playwright/test'
import { CODES } from './lang-url-helpers'
import { PUBLISHED } from './public-paths'

// PP8 (#897): every link a public page carries in its raw HTML (what a crawler follows) resolves, and
// no published page is left without a link to it from another public page (a link to itself does not count).

/** The internal `<a href>` targets of a served page, without fragment or query. */
function internalLinks(html: string): string[] {
  const found = new Set<string>()
  for (const tag of html.match(/<a\b[^>]*>/g) ?? []) {
    const href = /\bhref="([^"]*)"/.exec(tag)?.[1]
    if (href?.startsWith('/') && !href.startsWith('//')) found.add(href.split(/[?#]/)[0] ?? href)
  }
  return [...found]
}

for (const code of CODES) {
  test(`/${code}: every internal link of every public page resolves, and no page is orphaned`, async ({ request }) => {
    test.setTimeout(120_000)
    const linked = new Set<string>()
    const targets = new Set<string>()
    for (const page of PUBLISHED) {
      const response = await request.get(`/${code}${page}`)
      expect(response.status(), `/${code}${page}`).toBe(200)
      for (const href of internalLinks(await response.text())) {
        targets.add(href)
        // A page linking to itself (its own nav entry) does not make it reachable from another page.
        if (href !== `/${code}${page}`) linked.add(href)
      }
    }
    for (const href of targets) {
      const response = await request.get(href)
      expect(response.status(), `${href} (linked from a /${code} public page)`).toBe(200)
    }
    for (const page of PUBLISHED) {
      expect(linked.has(`/${code}${page}`), `/${code}${page} is linked from a public page`).toBe(true)
    }
  })
}
