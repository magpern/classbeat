import { expect, test, type Page } from '@playwright/test'
import { WORDS, type Code } from './lang-url-helpers'
import { signInThroughUi, signUpThroughUi, uniqueEmail } from './helpers'

// #1273: on a phone the first-visit cookie notice is a bar that takes room at the foot of the screen
// (the Landing hero is shortened by it), so it never covers the hero's title, lead or sign-up button,
// and above the tab bar on the signed-in layout it never covers the tab bar or the end of the page.
// `toBeVisible` cannot see a cover (#1271's finding), so these checks hit-test with elementFromPoint
// and compare boxes. The notice is showing in every case: this spec starts as a first-time visitor.
// The rules are in app.css and imagery.css (frontend/src/styles/cookieNoticePhone.test.ts, heroPhone.test.ts).
test.use({ storageState: { cookies: [], origins: [] } })

// The legal links at the foot of every page (LegalFooter.tsx): the last thing on a page.
const FOOTER = '#main-content > div > nav:last-child'

const SIZES = [
  { width: 360, height: 640 },
  { width: 390, height: 844 },
  { width: 412, height: 915 },
] as const
const CODES: Code[] = ['en', 'sv', 'da']

interface Box {
  x: number
  y: number
  width: number
  height: number
}

const overlap = (a: Box, b: Box) =>
  a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height

async function box(page: Page, selector: string): Promise<Box> {
  const found = await page.locator(selector).first().boundingBox()
  expect(found, `${selector} has a box`).not.toBeNull()
  return found!
}

/** Scrolls to the end of the page (again on each try: the page may still be growing) and waits for its last links to end above the notice. */
async function expectFooterAbove(page: Page, notice: Box): Promise<void> {
  await expect
    .poll(async () => {
      await page.evaluate(() => {
        window.scrollTo(0, document.documentElement.scrollHeight)
      })
      const footer = await box(page, FOOTER)
      return footer.y + footer.height
    }, { message: 'the page ends above the notice' })
    .toBeLessThanOrEqual(notice.y)
}

/** True when the topmost element at the middle of `selector` is it, or inside it. */
async function topmostAtCentre(page: Page, selector: string): Promise<boolean> {
  return page.evaluate((s) => {
    const element = document.querySelector(s)
    if (!element) return false
    const r = element.getBoundingClientRect()
    const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2)
    return hit !== null && element.contains(hit)
  }, selector)
}

for (const code of CODES) {
  for (const { width, height } of SIZES) {
    test(`the cookie notice does not cover the Landing hero on a ${String(width)}x${String(height)} phone in ${code}`, async ({
      page,
    }) => {
      await page.setViewportSize({ width, height })
      await page.goto(`/${code}`)
      await expect(page.getByLabel(WORDS[code].language)).toBeVisible()
      const notice = page.getByTestId('cookie-notice')
      await expect(notice).toBeVisible()
      const link = page.locator('.hero').getByRole('link', { name: WORDS[code].landingSignUp })
      await expect(link).toBeVisible()
      expect(await page.evaluate(() => window.scrollY)).toBe(0)

      // The bar is at the foot of the viewport and as wide as the page.
      const bar = await box(page, '[data-testid="cookie-notice"]')
      expect(bar.y + bar.height).toBeCloseTo(height, 0)
      expect(bar.x).toBe(0)

      // None of the hero's text or its button is under the bar: boxes apart, and the topmost element
      // at each one's centre is the element itself.
      for (const selector of ['.hero h1', '.hero__lead', '.hero a']) {
        const part = await box(page, selector)
        expect(overlap(part, bar), `${selector} is clear of the notice`).toBe(false)
        expect(part.y + part.height, `${selector} ends above the notice`).toBeLessThanOrEqual(bar.y)
        expect(await topmostAtCentre(page, selector), `${selector} is topmost at its centre`).toBe(true)
      }

      // The first screen of #1270 still holds above the bar: the button, and the hero, end in view.
      const hero = await box(page, '.hero')
      expect(hero.y + hero.height).toBeLessThanOrEqual(bar.y)

      // The button works with the notice still open, and the notice is neither dismissed nor moved by it.
      await link.click()
      await expect(page).toHaveURL(new RegExp(`/${code}/signup`))
      await expect(notice).toBeVisible()
      expect(await page.evaluate(() => window.localStorage.getItem('cb-cookie-notice'))).toBeNull()
    })
  }
}

test('the end of a page scrolls clear of the notice, and dismissing it gives the room back', async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 640 })
  await page.goto('/en')
  const notice = page.getByTestId('cookie-notice')
  await expect(notice).toBeVisible()
  const bar = await box(page, '[data-testid="cookie-notice"]')
  await expectFooterAbove(page, bar)

  await notice.getByRole('button', { name: 'OK' }).click()
  await expect(notice).toHaveCount(0)
  expect(await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--cb-notice-h'))).toBe('0px')
  const after = await box(page, FOOTER)
  expect(after.y + after.height, 'the page keeps no room for a notice that is gone').toBeGreaterThan(bar.y)
})

test('a wide screen keeps the corner card and reserves no room', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 })
  await page.goto('/en')
  const bar = await box(page, '[data-testid="cookie-notice"]')
  expect(bar.x).toBeGreaterThan(0)
  expect(bar.width).toBeLessThanOrEqual(20 * 16)
  expect(await page.evaluate(() => getComputedStyle(document.body).paddingBottom)).toBe('0px')
})

test('signed in on a phone the notice sits above the tab bar and covers neither it nor the end of the page', async ({
  page,
}) => {
  const email = uniqueEmail('cookie-phone')
  await signUpThroughUi(page, email)
  await signInThroughUi(page, email)
  for (const { width, height } of [SIZES[0], SIZES[1]]) {
    await page.setViewportSize({ width, height })
    // `/en` itself: `/` redirects there with a full page load, which would swap the page (and its
    // tab bar, which mounts only once the session is read) under the measurements below.
    await page.goto('/en')
    const notice = page.getByTestId('cookie-notice')
    await expect(notice).toBeVisible()
    const tabs = page.getByRole('navigation', { name: 'Main' })
    await expect(tabs).toBeVisible()
    // The tab bar mounts after the session is read and the notice follows it: wait for that to settle.
    // The distance between the two edges must reach 0; it is never 0 with no tab bar (the box call waits for it).
    await expect
      .poll(
        async () => {
          const noticeBox = await box(page, '[data-testid="cookie-notice"]')
          const tabBox = await box(page, '[data-tab-bar]')
          return Math.round(noticeBox.y + noticeBox.height - tabBox.y)
        },
        { message: 'the notice ends where the tab bar starts' },
      )
      .toBe(0)
    const bar = await box(page, '[data-testid="cookie-notice"]')
    const tabBar = await box(page, '[data-tab-bar]')
    expect(tabBar.y + tabBar.height).toBeCloseTo(height, 0)

    // Every tab, the raised + included (its top edge sticks out above the bar), is the topmost thing at its centre.
    for (const link of await tabs.getByRole('link').all()) {
      const handle = await link.elementHandle()
      const hit = await handle!.evaluate((a) => {
        const r = a.getBoundingClientRect()
        const top = document.elementFromPoint(r.left + r.width / 2, r.top + 4)
        const middle = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2)
        return a.contains(top) && a.contains(middle)
      })
      expect(hit, 'a tab is not covered by the notice').toBe(true)
    }

    // The end of the page scrolls above the notice.
    await expectFooterAbove(page, bar)
  }
})
