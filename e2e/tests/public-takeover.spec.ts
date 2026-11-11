import { expect, test } from '@playwright/test'
import { ALL_LANGUAGES, GATE_LANGUAGES, PAGES, SIZES, VISITS, pageName } from './public-takeover-matrix'
import { boxes, observe, seen, zoneColours } from './public-takeover-observe'
import { DIFF_LIMIT, compare, englishHeading, englishLeaks, noteDifference, shoot } from './public-takeover-visual'

// The takeover gate (docs/plans/public-pages.md, ADR 0030 section 7): a public page arrives
// prerendered, the scripts load late, and the client then takes it over. The person must not see a
// flash, a jump, the wrong theme's image, English on an sv or da page or a policy violation on the
// way. The matrix is in public-takeover-matrix.ts. Thresholds are starting values (ADR 0030).
//
// Script loading is held by the test (the entry script first, the other chunks, among them the
// catalogue, a little later), so the prerendered page is measured before takeover and again after.
//
// Cost: one navigation per cell, a fresh context per cell (the theme, the stored state and the
// viewport differ), and `parallel` mode so the cells can be split over shards (Playwright shards by
// test order, so they mostly stay together; one worker per shard, the fakes are shared state).

const BOX_TOLERANCE = 2
const SHIFT_LIMIT = 0.05
const ENTRY_HOLD_MS = 600
const CHUNK_HOLD_MS = 300

test.describe.configure({ mode: 'parallel' })

for (const lang of ALL_LANGUAGES) {
  for (const path of PAGES) {
    for (const size of SIZES) {
      for (const { theme, stored } of VISITS) {
        const visit = stored ? 'stored' : 'first visit'
        test(`/${lang}${path} (${theme}, ${visit}, ${size.name}px): takeover is quiet`, async ({ browser, baseURL }) => {
          test.skip(!GATE_LANGUAGES.includes(lang), `${lang} is not in GATE_LANGUAGES in public-takeover-matrix.ts`)
          const origin = new URL(baseURL ?? 'http://127.0.0.1:8000').origin
          const context = await browser.newContext({
            locale: lang,
            viewport: { width: size.width, height: size.height },
            colorScheme: theme,
            baseURL: origin,
            storageState: {
              cookies: [],
              origins: [
                {
                  origin,
                  localStorage: [
                    { name: 'cb-cookie-notice', value: '1' },
                    ...(stored ? [{ name: 'cb-theme', value: theme }] : []),
                  ],
                },
              ],
            },
          })
          const page = await context.newPage()
          await page.addInitScript(observe)

          const requested: string[] = []
          page.on('request', (r) => requested.push(r.url()))
          const pageErrors: string[] = []
          page.on('pageerror', (e) => pageErrors.push(e.message))
          // Chromium reports a refused resource or style in the console as well as in the event.
          const consoleViolations: string[] = []
          page.on('console', (m) => {
            if (/content security policy|refused to (apply|load|execute)/i.test(m.text())) consoleViolations.push(m.text().slice(0, 200))
          })

          // Hold the scripts: the entry first, then the other chunks (the catalogue among them).
          let release: () => void = () => undefined
          const gate = new Promise<void>((resolve) => {
            release = resolve
          })
          await page.route(/\/assets\/.*\.js(\?.*)?$/, async (route) => {
            const entry = /\/assets\/index-/.test(route.request().url())
            await gate
            await new Promise((r) => setTimeout(r, entry ? 0 : CHUNK_HOLD_MS))
            await route.continue()
          })

          const response = await page.goto(`/${lang}${path}`, { waitUntil: 'commit' })
          expect(response?.status()).toBe(200)
          await page.waitForSelector('#root h1')
          await page.waitForTimeout(ENTRY_HOLD_MS)

          // Before takeover: the prerendered page, with the chart's zone colours already in place.
          expect(await page.locator('html').getAttribute('data-theme')).toBe(stored ? theme : 'dark')
          const before = await boxes(page)
          const coloursBefore = await zoneColours(page)
          const h1Before = await page.locator('#root h1').textContent()
          expect(before.h1).not.toBeNull()
          if (path.startsWith('/examples/')) {
            expect(before.chart).not.toBeNull()
            expect(coloursBefore.length).toBeGreaterThan(0)
            expect(coloursBefore.every((c) => c !== '' && c !== 'rgb(0, 0, 0)')).toBe(true)
          }

          // English text: a scratch page parses the English page; the prerendered page must hold none
          // of its prose on sv/da, and its heading must differ.
          let leaksBefore: string[] = []
          if (lang !== 'en') {
            const scratch = await context.newPage()
            leaksBefore = await englishLeaks(context, scratch, page, path)
            expect.soft(h1Before?.trim(), 'the prerendered h1 is the English one').not.toBe(await englishHeading(context, scratch, path))
            await scratch.close()
          }

          const shot = `${lang}-${pageName(path)}-${theme}-${stored ? 'stored' : 'first'}-${size.name}`
          const shotBefore = await shoot(page, `${shot}-before`)

          // Mark the prerendered heading: `createRoot` replaces the tree, so the marker must be gone
          // afterwards and the node detached. If the client never mounts, both checks fail.
          const prerenderedH1 = await page.locator('#root h1').elementHandle()
          await prerenderedH1?.evaluate((el) => el.setAttribute('data-prerendered', '1'))

          release()
          await page.waitForLoadState('networkidle')
          await expect(page.locator('#root h1')).toHaveCount(1)
          await expect(page.locator('#root h1[data-prerendered]'), 'the client did not replace the prerendered heading').toHaveCount(0)
          expect(await prerenderedH1?.evaluate((el) => el.isConnected), 'the prerendered heading is still attached').toBe(false)
          await page.waitForTimeout(500)

          const after = await boxes(page)
          const coloursAfter = await zoneColours(page)
          const record = await seen(page)
          const shotAfter = await shoot(page, `${shot}-after`)

          expect.soft(record.emptyRoot, '#root was empty after the first paint').toBe(0)
          expect.soft(record.loadingCard, 'a loading card showed').toBe(0)
          expect(new Set(record.h1Values).size, `the h1 held ${JSON.stringify(record.h1Values)}`).toBe(1)
          expect(await page.locator('#root h1').textContent()).toBe(h1Before)
          expect.soft(record.violations, 'a CSP violation').toEqual([])
          expect.soft(consoleViolations, 'a CSP message in the console').toEqual([])
          expect(pageErrors, 'an uncaught error on the page').toEqual([])
          expect.soft(record.shifts, `layout shifts: ${JSON.stringify(record.shiftSources)}`).toBeLessThan(SHIFT_LIMIT)

          if (lang !== 'en') {
            const scratchAfter = await context.newPage()
            const leaksAfter = await englishLeaks(context, scratchAfter, page, path)
            await scratchAfter.close()
            expect.soft(leaksBefore, `English text on the prerendered ${lang} page`).toEqual([])
            expect.soft(leaksAfter, `English text on the taken-over ${lang} page`).toEqual([])
          }

          for (const key of ['h1', 'chart'] as const) {
            const a = before[key]
            const b = after[key]
            if (a === null) continue
            expect(b, `${key} is gone after takeover`).not.toBeNull()
            for (const side of ['x', 'y', 'width', 'height'] as const) {
              expect(Math.abs((b?.[side] ?? Infinity) - a[side]), `${key}.${side}`).toBeLessThanOrEqual(BOX_TOLERANCE)
            }
          }
          expect(coloursAfter).toEqual(coloursBefore)

          // No request for the other theme's imagery, and the images shown match the theme.
          const other = theme === 'dark' ? 'light' : 'dark'
          const wrong = requested.filter((u) => new RegExp(`/img/[^?]*-${other}-`).test(u))
          expect.soft(wrong, `the ${other} theme's images were requested`).toEqual([])
          const shown = await page.evaluate(() =>
            [...document.querySelectorAll('#root img')].map((i) => (i as HTMLImageElement).currentSrc),
          )
          for (const src of shown) expect(src).not.toMatch(new RegExp(`-${other}-`))

          // Before and after, image slots and the header's account area masked.
          const difference = await compare(context, shotBefore, shotAfter)
          if (difference.ratio > DIFF_LIMIT) noteDifference(shot, difference)
          expect(
            difference.ratio,
            `before and after differ in ${(difference.ratio * 100).toFixed(2)}% of pixels${difference.sizes ? ` (sizes ${difference.sizes})` : ''}`,
          ).toBeLessThanOrEqual(DIFF_LIMIT)

          await context.close()
        })
      }
    }
  }
}
