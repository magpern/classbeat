import { expect, test, type Page } from '@playwright/test'
import { WORDS, type Code } from './lang-url-helpers'

// #1270: on a phone the Landing's first screen (badge, title, lead and the sign-up button) is in view
// with no scroll. The viewports are the three the owner named; Swedish and Danish are checked at the
// smallest one, where their longer words and the three-row Swedish header put the most pressure on it.
// The rule itself is the hero's phone block in imagery.css (frontend/src/styles/heroPhone.test.ts).

const SIZES = [
  { width: 360, height: 640 },
  { width: 390, height: 844 },
  { width: 412, height: 915 },
] as const

const CASES: { code: Code; width: number; height: number }[] = [
  ...SIZES.map((size) => ({ code: 'en' as const, ...size })),
  { code: 'sv', width: 360, height: 640 },
  { code: 'da', width: 360, height: 640 },
]

async function expectFirstScreen(page: Page, code: Code, height: number) {
  const hero = page.locator('.hero')
  await expect(hero).toBeVisible()
  const link = hero.getByRole('link', { name: WORDS[code].landingSignUp })
  await expect(link).toBeVisible()
  await expect(page.locator('.hero .badge')).toBeVisible()
  await expect(page.locator('.hero h1')).toBeVisible()
  expect(await page.evaluate(() => window.scrollY)).toBe(0)
  const boxes = {
    badge: await page.locator('.hero .badge').boundingBox(),
    link: await link.boundingBox(),
    hero: await hero.boundingBox(),
  }
  expect(boxes.badge?.y ?? -1).toBeGreaterThanOrEqual(0)
  expect(boxes.link, 'the sign-up link has a box').not.toBeNull()
  // the button's bottom edge, and the hero's, are inside the viewport
  expect((boxes.link?.y ?? 0) + (boxes.link?.height ?? 0)).toBeLessThanOrEqual(height)
  expect((boxes.hero?.y ?? 0) + (boxes.hero?.height ?? 0)).toBeLessThanOrEqual(height)
}

for (const scheme of ['light', 'dark'] as const) {
  for (const { code, width, height } of CASES) {
    test(`the Landing hero fits a ${String(width)}x${String(height)} phone in ${code}, ${scheme}`, async ({ page }) => {
      await page.emulateMedia({ colorScheme: scheme })
      await page.setViewportSize({ width, height })
      await page.goto(`/${code}`)
      // the account nav (language, sign in, sign up) is part of the header's height: wait for it
      await expect(page.getByLabel(WORDS[code].language)).toBeVisible()
      await expectFirstScreen(page, code, height)
    })
  }
}

test('a wide screen keeps the hero as it was: not cut to the viewport', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 600 })
  await page.goto('/en')
  const box = await page.locator('.hero').boundingBox()
  expect(box?.height ?? 0).toBeGreaterThanOrEqual(24 * 16) // min-height: 24rem
})
