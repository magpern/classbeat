import { test, expect } from '@playwright/test'
import { signInThroughUi, signUpThroughUi, uniqueEmail } from './helpers'
import { THEMES, VIEWPORTS, setTheme, stackContext } from './mu-helpers'

// #346: the MU audits read computed colours with axe. Under reduced motion the app gives every
// element a 0.01 ms transition (tailwind.css), so for a frame or two after a theme switch the
// colours are the previous theme's: axe once saw a label as #15161b on #17181e (ratio 1.02). The
// audit must not sample until every finite transition and animation has finished, whatever the
// machine's speed. This pins that the helper's theme switch returns only when the page is at rest.
test('after a theme switch nothing is still transitioning, so axe reads the settled colours', async ({
  browser,
  baseURL,
}) => {
  test.setTimeout(120_000)
  const context = await stackContext(browser, baseURL!)
  const page = await context.newPage()
  const email = uniqueEmail('settle')
  await signUpThroughUi(page, email)
  await signInThroughUi(page, email)
  await page.goto('/account')
  await expect(page.getByRole('tab', { name: 'Connections' })).toBeVisible()
  await page.emulateMedia({ reducedMotion: 'reduce' })
  for (let round = 0; round < 3; round++) {
    for (const viewport of VIEWPORTS) {
      await page.setViewportSize({ width: viewport.width, height: viewport.height })
      for (const theme of THEMES) {
        await setTheme(page, theme)
        const moving = await page.evaluate(() =>
          document
            .getAnimations()
            .filter((a) => a.effect?.getComputedTiming().iterations !== Infinity)
            .map((a) => `${a.constructor.name} ${(a as CSSTransition).transitionProperty ?? ''}`),
        )
        expect(moving, `${viewport.name}, ${theme}, round ${String(round)}`).toEqual([])
      }
    }
  }
  await context.close()
})
