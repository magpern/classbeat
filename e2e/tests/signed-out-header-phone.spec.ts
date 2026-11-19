import { expect, test, type Page } from '@playwright/test'
import { CODES, WORDS, type Code } from './lang-url-helpers'

// #1272: the signed-out header (brand, language picker, Sign in, Sign up) is two rows at most on a phone
// in every language, and one row where it fits. In Swedish the three controls are 352 px wide, so on
// 360 px they used to wrap to a third row (165 px of header). The rule is the header's two class groups
// in components/shell/SignedOutHeader.tsx (frontend/src/components/shell/signedOutHeader.test.tsx).

const WIDTHS = [320, 360, 390, 412] as const
/** Two 44 px rows, the 8 px between them, the header's 8 px padding above and below, and its 1 px border. */
const TWO_ROWS_PX = 113

interface Controls {
  rows: number
  headerHeight: number
  /** the visible links and the select of the header, with their boxes */
  boxes: { name: string; left: number; right: number; top: number; height: number }[]
  overflow: number
}

/** Reads the header's controls (the site links are hidden on a phone and are not counted). */
async function readHeader(page: Page): Promise<Controls> {
  return page.evaluate(() => {
    const header = document.querySelector('header')
    if (!header) throw new Error('no header')
    const boxes = [...header.querySelectorAll<HTMLElement>('a, select')]
      .map((el) => ({ el, box: el.getBoundingClientRect() }))
      .filter(({ box }) => box.height > 0)
      .map(({ el, box }) => ({
        name: el.getAttribute('aria-label') ?? el.textContent.trim(),
        left: box.left,
        right: box.right,
        top: box.top,
        height: box.height,
      }))
    return {
      rows: new Set(boxes.map((b) => Math.round(b.top))).size,
      headerHeight: header.getBoundingClientRect().height,
      boxes,
      overflow: Math.max(document.documentElement.scrollWidth, document.body.scrollWidth) - window.innerWidth,
    }
  })
}

async function open(page: Page, code: Code, width: number, path = '') {
  await page.setViewportSize({ width, height: 700 })
  await page.goto(`/${code}${path}`)
  // the account controls arrive with the session: wait for the picker, which is part of the header's height
  await expect(page.getByLabel(WORDS[code].language)).toBeVisible()
}

for (const scheme of ['light', 'dark'] as const) {
  for (const code of CODES) {
    for (const width of WIDTHS) {
      test(`the signed-out header is two rows at most at ${String(width)} px in ${code}, ${scheme}`, async ({ page }) => {
        // the saved theme is what the page follows (dark by default, public/theme-init.js), not the browser's
        await page.addInitScript((theme) => {
          try {
            window.localStorage.setItem('cb-theme', theme)
          } catch {
            /* storage blocked: the default theme is checked instead */
          }
        }, scheme)
        await open(page, code, width)
        await expect(page.locator('html')).toHaveAttribute('data-theme', scheme)
        const header = await readHeader(page)
        // brand, language, Sign in, Sign up: all four there and none cut off
        expect(header.boxes).toHaveLength(4)
        expect(header.rows).toBeLessThanOrEqual(2)
        expect(header.headerHeight).toBeLessThanOrEqual(TWO_ROWS_PX)
        expect(header.overflow).toBeLessThanOrEqual(0)
        for (const box of header.boxes) {
          expect(box.left, box.name).toBeGreaterThanOrEqual(0)
          expect(box.right, box.name).toBeLessThanOrEqual(width)
          expect(box.height, `${box.name} is at least 44 px high`).toBeGreaterThanOrEqual(44)
        }
      })
    }
  }
}

test('Swedish on a 360 px phone: the brand and the language on one row, Sign in and Sign up on the next', async ({ page }) => {
  await open(page, 'sv', 360)
  const header = await readHeader(page)
  expect(header.rows).toBe(2)
  const byName = (name: string) => header.boxes.find((b) => b.name === name)
  const brand = byName('classbeat, startsida')
  const language = byName(WORDS.sv.language)
  const signIn = byName(WORDS.sv.signIn)
  expect(brand && language && signIn).toBeTruthy()
  expect(Math.round(brand?.top ?? -1)).toBe(Math.round(language?.top ?? -2))
  expect(signIn?.top ?? 0).toBeGreaterThan((brand?.top ?? 0) + (brand?.height ?? 0) - 1)
})

test('the sign-in and sign-up pages carry the same two-row header in Swedish on 360 px', async ({ page }) => {
  for (const path of ['/signin', '/signup']) {
    await open(page, 'sv', 360, path)
    expect((await readHeader(page)).rows, path).toBe(2)
  }
})

test('where the controls fit, the header is one row', async ({ page }) => {
  for (const code of CODES) {
    await open(page, code, 600)
    const header = await readHeader(page)
    expect(header.rows, `${code} at 600 px`).toBe(1)
  }
  await open(page, 'sv', 1280)
  expect((await readHeader(page)).rows).toBe(1)
})
