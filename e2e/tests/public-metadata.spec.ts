import { expect, test, type Page } from '@playwright/test'
import { CODES, WORDS, type Code } from './lang-url-helpers'
import { signUpAndIn } from './locale-helpers'
import { expectHeadIsServers } from './public-head'

// ADR 0030 sections 4 and 6: the server writes the head, and every move into or out of a public page
// is a real page load, so the head on screen is always the one a crawler gets. After each step the
// live title, description, canonical, hreflang and og:* equal a raw GET of that URL, each appears
// once, and `noindex` is on exactly the pages that are not published. The links are found by their
// address, so the specs need no words but the picker's.
test.setTimeout(180_000)

/** The text of the first h1 of a served page, without markup. */
function headingOf(html: string): string {
  const inner = /<h1\b[^>]*>([\s\S]*?)<\/h1>/.exec(html)?.[1] ?? ''
  return inner.replace(/<[^>]*>/g, '').trim()
}

/** The step: click the visible link to `/<code><path>` and wait for the page. */
async function follow(page: Page, code: Code, path: string): Promise<void> {
  const target = `/${code}${path}`
  await page.locator(`a[href="${target}"]:visible`).first().click()
  await page.waitForURL((url) => url.pathname === target)
  await settled(page)
}

async function settled(page: Page): Promise<void> {
  await page.waitForLoadState('load')
  await expect(page.getByRole('main')).toBeVisible()
  await expectHeadIsServers(page)
}

for (const code of CODES) {
  const words = WORDS[code]
  const other = CODES.find((c) => c !== code) ?? 'en'

  test.describe(`${words.name}, signed out`, () => {
    test.use({ locale: code })

    test('public to public, language switch, to app and back to public', async ({ page }) => {
      await page.goto(`/${code}`)
      await settled(page)
      await follow(page, code, '/privacy')
      await follow(page, code, '/terms')
      await follow(page, code, '/help')

      await page.getByLabel(words.language).selectOption(other)
      await page.waitForURL((url) => url.pathname === `/${other}/help`)
      await settled(page)
      await follow(page, other, '/terms')
      await page.getByLabel(WORDS[other].language).selectOption(code)
      await page.waitForURL((url) => url.pathname === `/${code}/terms`)
      await settled(page)

      await follow(page, code, '/signin') // public to app
      await follow(page, code, '/privacy') // app to public
    })

    test('back and forward cross the boundary as page loads', async ({ page }) => {
      await page.goto(`/${code}`)
      await follow(page, code, '/privacy')
      await follow(page, code, '/signin')
      await follow(page, code, '/terms')

      for (const path of ['/signin', '/privacy', '']) {
        await page.goBack()
        await page.waitForURL((url) => url.pathname === `/${code}${path}`)
        await settled(page)
      }
      for (const path of ['/privacy', '/signin', '/terms']) {
        await page.goForward()
        await page.waitForURL((url) => url.pathname === `/${code}${path}`)
        await settled(page)
      }
    })
  })

  test.describe(`${words.name}, signed in`, () => {
    test.use({ locale: code })

    test('through the footer: Home, public, app and public again, then back', async ({ page }) => {
      await signUpAndIn(page, words)
      // Signing in lands on the account page; Home is opened as a document, so its head is the server's.
      const landing = await (await page.request.get(`/${code}`)).text()
      const landingHeading = headingOf(landing)
      expect(landingHeading).not.toBe('')
      await page.goto(`/${code}`)
      // The server sends Landing's body; once the session answers the client replaces it with Home.
      await expect(page.getByRole('link', { name: words.design })).toBeVisible()
      await expect(page.getByRole('heading', { level: 1 })).not.toHaveText(landingHeading)
      await expect(page.getByRole('heading', { level: 1, name: landingHeading })).toHaveCount(0)
      await expectHeadIsServers(page)

      await follow(page, code, '/privacy') // Home to public
      await follow(page, code, '/classes') // public to app
      await follow(page, code, '/terms') // app to public
      await follow(page, code, '/help')

      await page.goBack()
      await page.waitForURL((url) => url.pathname === `/${code}/terms`)
      await settled(page)
      await page.goBack()
      await page.waitForURL((url) => url.pathname === `/${code}/classes`)
      await settled(page)
      await page.goForward()
      await page.waitForURL((url) => url.pathname === `/${code}/terms`)
      await settled(page)
    })
  })
}
