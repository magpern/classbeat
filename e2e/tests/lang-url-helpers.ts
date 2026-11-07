import { expect, type Page } from '@playwright/test'
import { passCaptcha } from './helpers'
import { signUpAndIn, type Words } from './locale-helpers'

// Shared by the language-prefixed URL specs (ADR 0027, #719): the three languages with the words
// the specs read (taken from the catalogues, so a wrong translation fails a spec that reads like a
// user), and the sign-up and sign-in steps in each of them.

export const CODES = ['en', 'sv', 'da'] as const
export type Code = (typeof CODES)[number]

export interface LangWords extends Words {
  code: Code
  /** language.label, the accessible name of the picker */
  language: string
  /** Svenska, Dansk, English: the picker's option for this language */
  name: string
}

export const WORDS: Readonly<Record<Code, LangWords>> = {
  en: {
    code: 'en',
    language: 'Language',
    name: 'English',
    landingSignUp: 'Sign up with an invite code',
    signIn: 'Sign in',
    createAccount: 'Create account',
    inviteCode: 'Invite code',
    email: 'Email',
    password: 'Password',
    checkInbox: 'Check your inbox',
    confirmSubject: 'Confirm your classbeat account',
    confirmAccount: 'Confirm account',
    accountCreated: 'Account created',
    design: 'Design a class',
    signOut: 'Sign out',
    signInFirst: 'Sign in first',
  },
  sv: {
    code: 'sv',
    language: 'Språk',
    name: 'Svenska',
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
  },
  da: {
    code: 'da',
    language: 'Sprog',
    name: 'Dansk',
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
  },
}

/** The path and query of the page, without origin and fragment: what a prefixed URL is judged by. */
export function where(page: Page): string {
  const url = new URL(page.url())
  return url.pathname + url.search
}

/** The page's URL, hash included (a mail token lives there). */
export function whereWithHash(page: Page): string {
  const url = new URL(page.url())
  return url.pathname + url.search + url.hash
}

/** Sign up, confirm and sign in in this language (the page's browser locale must match), then drop the session. */
export async function accountThenSignedOut(page: Page, w: LangWords): Promise<string> {
  const email = await signUpAndIn(page, w)
  await page.context().clearCookies()
  return email
}

/** Fill the sign-in form on the page you are on and submit it. */
export async function submitSignIn(page: Page, w: LangWords, email: string, password: string): Promise<void> {
  await page.getByLabel(w.email, { exact: true }).fill(email)
  await page.getByLabel(w.password, { exact: true }).fill(password)
  await passCaptcha(page)
  await page.getByRole('button', { name: w.signIn, exact: true }).click()
}

export async function expectSignedIn(page: Page, email: string): Promise<void> {
  await expect(page.getByRole('button', { name: email })).toBeVisible()
}
