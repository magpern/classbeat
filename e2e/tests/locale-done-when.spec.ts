import { execFileSync } from 'node:child_process'
import type { Browser, Page } from '@playwright/test'
import {
  PASSWORD,
  expect,
  linkFromMail,
  randomClientIp,
  signInThroughUi,
  signUpThroughUi,
  test,
  uniqueEmail,
} from './helpers'
import { MOWL_PASSWORD, PLAYLISTS, connectMowl, fakeMowl, jobId, startDesign } from './design-helpers'
import { signUpAndIn, type Words } from './locale-helpers'
import { sql } from './song-helpers'

// ML8, the done-when of milestone ML (ADR 0023, docs/plans/multilingual.md): the language a person
// picks logged out is kept by the new account and sets its mail; the account's language follows
// them to a new browser; "Follow this browser" follows the browser; a class designed on the recorded
// model keeps its English text inside a Swedish page; and CLASSBEAT_LOCALES narrows what is offered.
// The words are the catalogues' own, written out so a wrong or missing translation fails here. The
// specs run on the fake MOWL and the recorded model; mail is read from Mailpit.
test.describe.configure({ mode: 'serial' })
test.setTimeout(180_000)
const LONG = { timeout: 60_000 }

const da: Words = {
  code: 'da',
  landingSignUp: 'Opret dig med en invitationskode',
  signIn: 'Log ind',
  createAccount: 'Opret konto',
  inviteCode: 'Invitationskode',
  email: 'Mail',
  password: 'Adgangskode',
  checkInbox: 'Tjek din indbakke',
  confirmSubject: 'Bekræft din classbeat-konto',
  confirmAccount: 'Bekræft konto',
  accountCreated: 'Kontoen er oprettet',
  design: 'Design et hold',
  signOut: 'Log ud',
  signInFirst: 'Log ind først',
}
const DA = {
  language: 'Sprog',
  resetSend: 'Send nulstillingslink',
  resetSubject: 'Nulstil din classbeat-adgangskode',
}
const SV = {
  language: 'Språk',
  follow: 'Följ den här webbläsaren',
  design: 'Utforma ett pass',
  ready: 'Ditt pass är klart',
}

/** The account's saved language, straight from the database (`''` is NULL: follow the browser). */
function savedLocale(email: string): string {
  return sql(`SELECT coalesce(locale, '') FROM users WHERE email = '${email}'`)
}

/** A browser with nothing saved: its own language, its own client address, no cookies. */
async function freshBrowser(browser: Browser, baseURL: string | undefined, locale: string): Promise<Page> {
  const context = await browser.newContext({
    baseURL,
    locale,
    extraHTTPHeaders: { 'X-Forwarded-For': randomClientIp() },
  })
  return context.newPage()
}

/** Sign in on the sign-in page, whatever language it is in (the labels are given). */
async function signInAs(
  page: Page,
  email: string,
  labels: { email: string; password: string; button: string },
) {
  await page.goto('/signin')
  await page.getByLabel(labels.email, { exact: true }).fill(email)
  await page.getByLabel(labels.password, { exact: true }).fill(PASSWORD)
  await page.getByRole('button', { name: labels.button, exact: true }).click()
  await expect(page.getByRole('button', { name: email })).toBeVisible()
}

const html = (page: Page) => page.locator('html')
let daEmail = ''

test('Danish picked logged out: the account keeps it and its mail is Danish', async ({ page }) => {
  await page.goto('/')
  await expect(html(page)).toHaveAttribute('lang', 'en')
  await page.getByRole('combobox', { name: 'Language' }).selectOption('da')
  await expect(html(page)).toHaveAttribute('lang', 'da')

  // Sign-up, the confirmation mail (subject in Danish, read from Mailpit), confirm and sign in.
  daEmail = await signUpAndIn(page, da)
  expect(savedLocale(daEmail)).toBe('da')

  // A later password-reset mail is Danish too: the account's language sets it.
  await page.getByRole('button', { name: daEmail }).click()
  await page.getByRole('menuitem', { name: da.signOut }).click()
  await expect(page.getByRole('heading', { name: da.signIn, exact: true })).toBeVisible()
  await page.goto('/reset')
  await page.getByLabel(da.email, { exact: true }).fill(daEmail)
  await page.getByRole('button', { name: DA.resetSend }).click()
  await expect(page.getByRole('heading', { name: da.checkInbox })).toBeVisible()
  await linkFromMail(page.request, daEmail, DA.resetSubject)
})

test('Swedish chosen on the account page follows the account to a fresh browser', async ({
  browser,
  baseURL,
}) => {
  // A browser that prefers English signs in to the Danish account and gets Danish.
  const first = await freshBrowser(browser, baseURL, 'en')
  await signInAs(first, daEmail, { email: 'Email', password: 'Password', button: 'Sign in' })
  await expect(html(first)).toHaveAttribute('lang', 'da')
  await first.goto('/')
  await expect(first.getByRole('link', { name: da.design })).toBeVisible()

  // On the account page the language is switched to Swedish; the page changes at once and it is saved.
  await first.goto('/account#appearance')
  await first.getByRole('combobox', { name: DA.language }).selectOption('sv')
  await expect(html(first)).toHaveAttribute('lang', 'sv')
  await expect(first.getByRole('combobox', { name: SV.language })).toHaveValue('sv')
  await expect.poll(() => savedLocale(daEmail)).toBe('sv')
  await first.context().close()

  // Another fresh browser (English, nothing saved) is Swedish once it has signed in.
  const second = await freshBrowser(browser, baseURL, 'en')
  await second.goto('/signin')
  await expect(html(second)).toHaveAttribute('lang', 'en')
  await signInAs(second, daEmail, { email: 'Email', password: 'Password', button: 'Sign in' })
  await expect(html(second)).toHaveAttribute('lang', 'sv')
  await second.goto('/')
  await expect(second.getByRole('link', { name: SV.design })).toBeVisible()

  // "Follow this browser" saves nothing on the account and the page follows the browser (English).
  await second.goto('/account#appearance')
  await second.getByRole('combobox', { name: SV.language }).selectOption({ label: SV.follow })
  await expect(html(second)).toHaveAttribute('lang', 'en')
  await expect.poll(() => savedLocale(daEmail)).toBe('')
  await second.context().close()

  // A Danish browser then gets Danish, a Swedish one Swedish: the account no longer decides.
  const danish = await freshBrowser(browser, baseURL, 'da-DK')
  await signInAs(danish, daEmail, { email: da.email, password: da.password, button: da.signIn })
  await expect(html(danish)).toHaveAttribute('lang', 'da')
  await danish.goto('/')
  await expect(danish.getByRole('link', { name: da.design })).toBeVisible()
  await danish.context().close()
  const swedish = await freshBrowser(browser, baseURL, 'sv-SE')
  await signInAs(swedish, daEmail, { email: 'E-post', password: 'Lösenord', button: 'Logga in' })
  await expect(html(swedish)).toHaveAttribute('lang', 'sv')
  await swedish.goto('/')
  await expect(swedish.getByRole('link', { name: SV.design })).toBeVisible()
  await swedish.context().close()
})

test('a class designed on the recorded model shows a Swedish page around English class text', async ({
  page,
}) => {
  fakeMowl('arm', PLAYLISTS.draft.id)
  const email = uniqueEmail('ml8-class')
  await signUpThroughUi(page, email)
  await signInThroughUi(page, email)
  await connectMowl(page)
  const id = await startDesign(page, 'ML8 draft', PLAYLISTS.draft.name)
  await expect(page.getByRole('heading', { name: 'Your class is ready' })).toBeVisible(LONG)
  await expect(page.getByRole('heading', { name: 'Invented Tuesday climb' })).toBeVisible()

  await page.goto('/account#appearance')
  await page.getByRole('combobox', { name: 'Language' }).selectOption('sv')
  await expect(html(page)).toHaveAttribute('lang', 'sv')
  await expect.poll(() => savedLocale(email)).toBe('sv')

  // The same job, now in Swedish: the page is Swedish, what the model wrote stays English (ADR 0023).
  await page.goto(`/jobs/${id}`)
  expect(jobId(page)).toBe(id)
  await expect(page.getByRole('heading', { name: SV.ready })).toBeVisible(LONG)
  await expect(page.getByRole('heading', { name: 'Invented Tuesday climb' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Your class is ready' })).toHaveCount(0)
  expect(await page.content()).not.toContain(MOWL_PASSWORD)
})

test('CLASSBEAT_LOCALES=en offers English only', async ({ page }) => {
  // The server: the web container's own settings with the variable set offer only English.
  const offered = execFileSync(
    'docker',
    [
      'compose',
      'exec',
      '-T',
      '-e',
      'CLASSBEAT_LOCALES=en',
      'web',
      'python',
      '-c',
      'from classbeat.config import Settings; from classbeat.locales import offered_locales; print(",".join(offered_locales(Settings())))',
    ],
    { cwd: new URL('../..', import.meta.url), encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] },
  ).trim()
  expect(offered).toBe('en')

  // The default stack offers all three, so the picker is there ...
  await page.goto('/')
  await expect(page.getByRole('combobox', { name: 'Language' })).toBeVisible()
  await expect(page.getByRole('combobox', { name: 'Language' }).locator('option')).toHaveText([
    'English',
    'Svenska',
    'Dansk',
  ])

  // ... and when the session answer lists English only, as that server's would, it is gone.
  await page.route('**/api/auth/session', async (route) => {
    const response = await route.fetch()
    const body = (await response.json()) as Record<string, unknown>
    await route.fulfill({ response, json: { ...body, locales: ['en'] } })
  })
  await page.goto('/')
  await expect(page.getByRole('link', { name: 'Sign in' }).first()).toBeVisible()
  await expect(page.getByRole('combobox', { name: 'Language' })).toHaveCount(0)
})
