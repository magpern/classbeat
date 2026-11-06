import { expect, test } from '@playwright/test'
import { auditPage, expectValidLang, signUpAndIn, type Words } from './locale-helpers'

// ML7 done-when, Swedish (ADR 0023): a browser that prefers Swedish gets the whole main flow in it: the landing
// page, sign-up with its mail, sign-in, home and sign-out. The pages are audited once at 390 px (the
// audit helper also looks at 1440) with axe, and the html element has a valid lang. The words are
// the catalogue's own; the saved choice and the pre-paint lang are the browser's, not the account's.
test.describe.configure({ mode: 'serial' })
test.use({ locale: 'sv-SE' })
test.setTimeout(240_000)

const w: Words = {
  code: 'sv',
  landingSignUp: 'Registrera dig med en inbjudningskod',
  signIn: 'Logga in',
  createAccount: 'Skapa konto',
  inviteCode: 'Inbjudningskod',
  email: 'E-post',
  password: 'Lösenord',
  checkInbox: 'Kolla din inkorg',
  confirmSubject: 'Bekräfta ditt classbeat-konto',
  confirmAccount: 'Bekräfta konto',
  accountCreated: 'Kontot är skapat',
  design: 'Utforma ett pass',
  signOut: 'Logga ut',
  signInFirst: 'Logga in först',
}

test('the pages before sign-in are in Swedish and have a valid lang', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('link', { name: w.landingSignUp })).toBeVisible()
  await expectValidLang(page, w.code)
  await auditPage(page, 'landing', page.getByRole('link', { name: w.landingSignUp }))
  await page.goto('/signin')
  await expect(page.getByRole('button', { name: w.signIn, exact: true })).toBeVisible()
  await expectValidLang(page, w.code)
  await auditPage(page, 'sign in', page.getByRole('button', { name: w.signIn, exact: true }))
  await page.goto('/signup')
  await expect(page.getByRole('button', { name: w.createAccount })).toBeVisible()
  await auditPage(page, 'sign up', page.getByRole('button', { name: w.createAccount }))
})

test('sign up, mail, sign in, home and sign out in Swedish', async ({ page }) => {
  const email = await signUpAndIn(page, w)
  await page.goto('/')
  await expect(page.getByRole('link', { name: w.design })).toBeVisible()
  await expectValidLang(page, w.code)
  await auditPage(page, 'home', page.getByRole('link', { name: w.design }))
  await page.goto('/account')
  await expect(page.getByRole('tab').first()).toBeVisible()
  await auditPage(page, 'account', page.getByRole('tab').first())

  await page.getByRole('button', { name: email }).click()
  await page.getByRole('menuitem', { name: w.signOut }).click()
  await expect(page.getByRole('heading', { name: w.signIn, exact: true })).toBeVisible()
  await page.goto('/account')
  await expect(page.getByRole('heading', { name: w.signInFirst })).toBeVisible()
})
