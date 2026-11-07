import AxeBuilder from '@axe-core/playwright'
import { expect, type Page } from '@playwright/test'
import { PASSWORD, createInvite, linkFromMail, passCaptcha, uniqueEmail } from './helpers'
import { audit, type Findings } from './mu-helpers'

// Shared by the Swedish and Danish specs (ML7, ADR 0023): the words are written out here, taken from
// the catalogue, so a wrong or missing translation fails a spec that reads like a user.

export interface Words {
  code: 'sv' | 'da'
  /** landing.signUp */
  landingSignUp: string
  /** auth.signIn, the sign-in heading and button */
  signIn: string
  /** auth.createAccountSubmit */
  createAccount: string
  /** auth.inviteCode */
  inviteCode: string
  /** common.email */
  email: string
  /** common.password */
  password: string
  /** auth.checkInbox */
  checkInbox: string
  /** the mail subject of SUBJECT_SIGNUP_CONFIRM (ML6 catalogue) */
  confirmSubject: string
  /** confirm.submit */
  confirmAccount: string
  /** confirm.doneTitle */
  accountCreated: string
  /** home.design.button */
  design: string
  /** shell.menu.signOut */
  signOut: string
  /** shell.requireAuth.title */
  signInFirst: string
}

/** Sign up, confirm by the mailed link (in this language) and sign in, all through the UI. */
export async function signUpAndIn(page: Page, w: Words): Promise<string> {
  const email = uniqueEmail(`ml7-${w.code}`)
  await page.goto('/')
  await expect(page.locator('html')).toHaveAttribute('lang', w.code)
  await page.getByRole('link', { name: w.landingSignUp }).click()
  await page.getByLabel(w.inviteCode).fill(createInvite())
  await page.getByLabel(w.email, { exact: true }).fill(email)
  await page.getByLabel(w.password, { exact: true }).fill(PASSWORD)
  await passCaptcha(page)
  await page.getByRole('button', { name: w.createAccount }).click()
  await expect(page.getByRole('heading', { name: w.checkInbox })).toBeVisible()

  // The mail is in the language chosen at sign-up (ML6), so its subject is the translated one.
  await page.goto(await linkFromMail(page.request, email, w.confirmSubject))
  await page.getByLabel(w.password, { exact: true }).fill(PASSWORD)
  await page.getByRole('button', { name: w.confirmAccount }).click()
  await expect(page.getByRole('heading', { name: w.accountCreated })).toBeVisible()

  await page.goto('/signin')
  await page.getByLabel(w.email, { exact: true }).fill(email)
  await page.getByLabel(w.password, { exact: true }).fill(PASSWORD)
  await passCaptcha(page)
  await page.getByRole('button', { name: w.signIn, exact: true }).click()
  await expect(page.getByRole('button', { name: email })).toBeVisible()
  return email
}

/** axe's two language rules, on the page you are on: the html element has a valid lang. */
export async function expectValidLang(page: Page, code: string): Promise<void> {
  await expect(page.locator('html')).toHaveAttribute('lang', code)
  const result = await new AxeBuilder({ page })
    .withRules(['html-has-lang', 'html-lang-valid', 'valid-lang'])
    .analyze()
  expect(result.violations.map((v) => v.id)).toEqual([])
}

export async function auditPage(
  page: Page,
  name: string,
  primary: ReturnType<Page['locator']>,
): Promise<Findings> {
  const result = await audit(page, name, primary)
  expect.soft(result.problems, name).toEqual([])
  return result
}
