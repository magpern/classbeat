import { test, expect, type BrowserContext, type Page } from '@playwright/test'
import { PASSWORD, signInThroughUi, signUpThroughUi, uniqueEmail } from './helpers'
import { PLAYLISTS, connectMowl, fakeMowl, getJob, jobId } from './design-helpers'
import { useModelScript } from './model-script'
import {
  audit,
  grantAdmin,
  stackContext,
  totpCode,
  watchConsole,
  type Findings,
} from './mu-helpers'

// MU done-when (roadmap Amendment 8, ADR 0013 section 10), part 1: every page of the app on the
// compose stack with the fakes, at 390 and 1440 px in the dark and the light theme. Each page gets
// no sideways scroll, a visible primary action, no serious or critical axe violation, and the
// browser reports no console error (so no CSP violation) while it is shown. One signed-in browser
// walks through the journey: sign up, connect MOWL, design a class (held mid-run, then finished),
// open it, and the account, music and admin pages. Songs for approval and the covers are
// mu-covers.spec.ts.
test.describe.configure({ mode: 'serial' })
test.setTimeout(240_000)
const LONG = { timeout: 60_000 }

let context: BrowserContext
let page: Page
let consoleErrors: string[]
const findings: Findings[] = []

test.beforeAll(async ({ browser, baseURL }) => {
  context = await stackContext(browser, baseURL!)
  page = await context.newPage()
  consoleErrors = watchConsole(page)
})

test.afterAll(async () => {
  await context.close()
})

/** Audit the page you are on, remember the findings, and require a quiet console. */
async function check(name: string, primary: ReturnType<Page['locator']>) {
  const before = consoleErrors.length
  const result = await audit(page, name, primary)
  findings.push(result)
  expect.soft(result.problems, `${name}`).toEqual([])
  expect.soft(consoleErrors.slice(before), `${name}: console`).toEqual([])
}

async function visit(path: string, ready: ReturnType<Page['locator']>) {
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto(path)
  await expect(ready.first()).toBeVisible(LONG)
}

test('the pages before sign-in', async () => {
  await visit('/', page.getByRole('link', { name: 'Sign up with an invite code' }))
  await check('landing', page.getByRole('link', { name: 'Sign up with an invite code' }))
  await visit('/signin', page.getByRole('button', { name: 'Sign in', exact: true }))
  await check('sign in', page.getByRole('button', { name: 'Sign in', exact: true }))
  await visit('/signup', page.getByRole('button', { name: 'Create account' }))
  await check('sign up', page.getByRole('button', { name: 'Create account' }))
  await visit('/reset', page.getByRole('main').getByRole('button').first())
  await check('reset', page.getByRole('main').getByRole('button').first())
})

test('the signed-in pages: home, classes, new class, designing, ready, class', async () => {
  const email = uniqueEmail('mu')
  fakeMowl('arm', PLAYLISTS.slow.id)
  await signUpThroughUi(page, email)
  await signInThroughUi(page, email)
  await connectMowl(page)

  await visit('/', page.getByRole('link', { name: 'Design a class' }))
  await check('home', page.getByRole('link', { name: 'Design a class' }))
  await visit('/classes', page.getByRole('link', { name: 'Design a class' }))
  await check('my classes (empty)', page.getByRole('link', { name: 'Design a class' }))

  await visit('/classes/new', page.getByRole('button', { name: 'Design my class' }))
  const form = page.getByRole('form', { name: 'New class' })
  await check('new class', form.getByRole('button', { name: 'Design my class' }))

  await form.getByLabel('Class name').fill('MU done-when')
  await form.getByRole('radio', { name: PLAYLISTS.slow.name }).check({ force: true })
  await form.getByRole('button', { name: 'Design my class' }).click()
  await expect(page).toHaveURL(/\/jobs\/[0-9a-f-]{36}$/)
  const id = jobId(page)

  // The run is certainly in flight: the fake holds its first read of the playlist.
  await expect.poll(() => fakeMowl('state', PLAYLISTS.slow.id).held, LONG).toBe(1)
  await expect(page.getByRole('button', { name: 'Stop designing' })).toBeVisible()
  await check('designing', page.getByRole('button', { name: 'Stop designing' }))

  // Let it finish with the recorded draft.
  useModelScript('good_first_draft')
  fakeMowl('release', PLAYLISTS.slow.id)
  await expect(page.getByRole('heading', { name: 'Your class is ready' })).toBeVisible(LONG)
  expect((await getJob(page, id)).state).toBe('completed')
  await check('your class is ready', page.getByRole('main').getByRole('link').first())

  await visit('/classes', page.getByRole('link', { name: 'MU done-when' }))
  await check('my classes', page.getByRole('link', { name: 'Design a class' }))
  await page.getByRole('link', { name: 'MU done-when' }).first().click()
  await expect(page).toHaveURL(/\/classes\/[0-9a-f-]{36}$/)
  await expect(page.getByRole('heading', { name: 'MU done-when', level: 1 })).toBeVisible()
  await check('class', page.getByTestId('save-panel').getByRole('button').first())
})

test('the account tabs, music and Spotify pages', async () => {
  const tabs = ['Connections', 'Plan and usage', 'Sign-in and security', 'Your data', 'Appearance']
  await visit('/account', page.getByRole('tab', { name: 'Connections' }))
  for (const label of tabs) {
    await page.setViewportSize({ width: 1440, height: 900 })
    const tab = page.getByRole('tab', { name: label })
    await tab.click()
    await expect(tab).toHaveAttribute('aria-selected', 'true')
    await check(`account: ${label}`, tab)
  }
  await visit('/music', page.getByRole('heading', { name: 'Music', level: 1 }))
  await check('music', page.getByRole('heading', { name: 'Music', level: 1 }))
  await visit('/account/spotify', page.getByRole('main').getByRole('button').first())
  await check('spotify', page.getByRole('main').getByRole('button').first())
})

test('the admin pages, signed in as an admin with a TOTP code', async () => {
  // A second user, made admin by the operator command (which ends their sessions).
  const adminEmail = uniqueEmail('mu-admin')
  await page.context().clearCookies()
  await signUpThroughUi(page, adminEmail)
  grantAdmin(adminEmail)
  // An admin is not sent to the account page on sign-in: wait for the shell, not for a heading.
  await page.goto('/signin')
  await page.getByLabel('Email', { exact: true }).fill(adminEmail)
  await page.getByLabel('Password', { exact: true }).fill(PASSWORD)
  await page.getByRole('button', { name: 'Sign in', exact: true }).click()
  await expect(page.getByRole('navigation', { name: 'Main' })).toBeVisible(LONG)

  // Turning on two-factor (the key is on the page as text, the fake authenticator is totpCode)
  // passes the admin's TOTP check for this session: /admin then opens.
  await page.goto('/account#security')
  await page.getByRole('button', { name: 'Set up an authenticator app' }).click()
  const key = (await page.locator('code').first().innerText()).trim()
  await page.getByLabel('Code from the app').fill(totpCode(key))
  await page.getByRole('button', { name: 'Turn on two-factor' }).click()
  await page.getByRole('button', { name: 'I have saved them' }).click()
  await page.goto('/admin')
  await expect(page.getByRole('navigation', { name: 'Admin' })).toBeVisible(LONG)

  const sections: [string, string][] = [
    ['/admin', 'Users'],
    ['/admin/codes', 'Codes'],
    ['/admin/usage', 'Usage'],
    ['/admin/jobs', 'Jobs'],
    ['/admin/audit', 'Audit log'],
    ['/admin/settings', 'Settings'],
  ]
  const nav = (label: string) => page.getByRole('navigation', { name: 'Admin' }).getByRole('link', { name: label })
  for (const [path, label] of sections) {
    await visit(path, nav(label))
    await expect(nav(label)).toHaveAttribute('aria-current', 'page')
    await check(`admin: ${label}`, nav(label))
  }
})

test('every page was audited', () => {
  // 4 before sign-in, 7 signed-in, 5 account tabs, music, Spotify, 6 admin sections.
  expect(findings.map((f) => f.page)).toHaveLength(24)
})
