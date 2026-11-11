import { expect, test, type Page } from '@playwright/test'
import { LISTED } from './public-paths'

// The takeover gate (docs/plans/public-pages.md, ADR 0030 section 5): a public page arrives
// prerendered, the scripts load late, and the client then takes it over. The person must not see a
// flash, a jump, the wrong theme's image or a policy violation on the way. PP5 runs it in English
// on the draft pages; PP7 adds sv and da. Thresholds are starting values (ADR 0030).
//
// Script loading is held by the test (the entry script first, the other chunks, among them the
// catalogue, a little later), so the prerendered page is measured before takeover and again after.

const THEMES = ['dark', 'light'] as const
const SIZES = [
  { name: '390', width: 390, height: 844 },
  { name: '1440', width: 1440, height: 900 },
]
const SHIFT_LIMIT = 0.05
const BOX_TOLERANCE = 2
const ENTRY_HOLD_MS = 600
const CHUNK_HOLD_MS = 300

// Landing, How it works (when listed) and one example with a chart.
const PAGES = ['', '/how-it-works', '/examples/hill-challenge'].filter((p) => LISTED.includes(p))

interface Seen {
  emptyRoot: number
  loadingCard: number
  h1Values: string[]
  shifts: number
  violations: string[]
}

/** Runs before any page script: records what the person could see go wrong. */
function observe(): void {
  const seen: Seen = { emptyRoot: 0, loadingCard: 0, h1Values: [], shifts: 0, violations: [] }
  ;(window as unknown as { __seen: Seen }).__seen = seen
  document.addEventListener('securitypolicyviolation', (e) => {
    seen.violations.push(`${e.violatedDirective} ${e.blockedURI}`)
  })
  try {
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries() as unknown as { value: number; hadRecentInput: boolean }[]) {
        if (!entry.hadRecentInput) seen.shifts += entry.value
      }
    }).observe({ type: 'layout-shift', buffered: true })
  } catch {
    // layout-shift is Chromium only; the spec runs there
  }
  let painted = false
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      painted = true
    })
  })
  const look = () => {
    const root = document.getElementById('root')
    if (!root) return
    if (painted && root.firstElementChild === null) seen.emptyRoot++
    if (painted && /^\s*Loading\s*$/.test(root.textContent ?? '')) seen.loadingCard++
    const h1 = root.querySelector('h1')?.textContent?.trim()
    if (h1 && seen.h1Values.at(-1) !== h1) seen.h1Values.push(h1)
  }
  new MutationObserver(look).observe(document, { childList: true, subtree: true, characterData: true })
  document.addEventListener('DOMContentLoaded', look)
}

async function seen(page: Page): Promise<Seen> {
  return page.evaluate(() => (window as unknown as { __seen: Seen }).__seen)
}

/** The boxes that must not move: the heading and, where there is one, the chart. */
async function boxes(page: Page) {
  return page.evaluate(() => {
    const box = (el: Element | null) => {
      const r = el?.getBoundingClientRect()
      return r ? { x: r.x, y: r.y + window.scrollY, width: r.width, height: r.height } : null
    }
    return { h1: box(document.querySelector('#root h1')), chart: box(document.querySelector('#root [data-testid="profile"]')) }
  })
}

/** The colour the first stop of each zone gradient resolves to: the bars' zone colours. */
async function zoneColours(page: Page): Promise<string[]> {
  return page.evaluate(() =>
    [...document.querySelectorAll('#root svg linearGradient')].map((g) => {
      const stop = g.querySelector('stop')
      return stop ? getComputedStyle(stop).stopColor : ''
    }),
  )
}

for (const theme of THEMES) {
  for (const size of SIZES) {
    for (const path of PAGES) {
      for (const stored of [true, false]) {
        // A stored theme is read by theme-init.js; a first visit follows the default (dark).
        if (!stored && theme === 'light') continue
        test(`/en${path} (${theme}${stored ? ', stored' : ', first visit'}, ${size.name}px): takeover is quiet`, async ({ browser, baseURL }) => {
          const context = await browser.newContext({
            locale: 'en',
            viewport: { width: size.width, height: size.height },
            colorScheme: theme,
            storageState: {
              cookies: [],
              origins: [
                {
                  origin: new URL(baseURL ?? 'http://127.0.0.1:8000').origin,
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

          const url = `/en${path}`
          const response = await page.goto(url, { waitUntil: 'commit' })
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

          release()
          await page.waitForLoadState('networkidle')
          // The app has taken over once a client-only part of the shell exists.
          await expect(page.locator('#root h1')).toHaveCount(1)
          await page.waitForTimeout(500)

          const after = await boxes(page)
          const coloursAfter = await zoneColours(page)
          const record = await seen(page)

          expect(record.emptyRoot, '#root was empty after the first paint').toBe(0)
          expect(record.loadingCard, 'a loading card showed').toBe(0)
          expect(new Set(record.h1Values).size, `the h1 held ${JSON.stringify(record.h1Values)}`).toBe(1)
          expect(await page.locator('#root h1').textContent()).toBe(h1Before)
          expect(record.violations, 'a CSP violation').toEqual([])
          expect(record.shifts).toBeLessThan(SHIFT_LIMIT)

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
          expect(wrong, `the ${other} theme's images were requested`).toEqual([])
          const shown = await page.evaluate(() =>
            [...document.querySelectorAll('#root img')].map((i) => (i as HTMLImageElement).currentSrc),
          )
          for (const src of shown) expect(src).not.toMatch(new RegExp(`-${other}-`))

          await context.close()
        })
      }
    }
  }
}
