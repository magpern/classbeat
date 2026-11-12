import { expect, PASSWORD, signInThroughUi, signUpThroughUi, test, uniqueEmail } from './helpers'
import { requestInviteThroughUi } from './invite-request-helpers'
import { enrollTotp, newUser, stepUp } from './m5-helpers'
import { stackContext } from './mu-helpers'

// PB-8 (#986, ADR 0033) on the compose stack, fakes only: a visitor requests an invite from the
// sign-up screen; the admin approves it with a tier on the Invite requests screen, the code is
// shown once; the visitor signs up with that code and the new account has the tier. A second
// request is declined and moves to the Declined tab. The sign-up mode of the stack is `invite`.
test.setTimeout(300_000)

test('a request becomes an invite with a tier, and the new account has that tier', async ({ browser, baseURL }) => {
  const admin = await newUser(browser, baseURL, 'invite-req-admin', false, true)
  const secret = await enrollTotp(admin.page)
  const visitorContext = await stackContext(browser, baseURL!)
  const visitor = await visitorContext.newPage()
  const email = uniqueEmail('requester')
  const declined = uniqueEmail('declined')

  await requestInviteThroughUi(visitor, email)
  await requestInviteThroughUi(visitor, declined, 'a poster', 'No')

  // The admin sees both, with the text exactly as typed, and the queue against the cap.
  const page = admin.page
  await page.goto('/admin/invite-requests')
  await expect(page.getByRole('heading', { name: 'Invite requests' })).toBeVisible()
  const row = page.getByRole('row').filter({ hasText: email })
  await expect(row).toContainText('a friend at the studio')
  await expect(page.getByTestId('queue-state')).toContainText('of 1000')

  // Decline one, after confirming (#989): it moves to the Declined tab.
  await page.getByRole('row').filter({ hasText: declined }).getByRole('button', { name: 'Decline' }).click()
  await page.getByRole('group', { name: 'Confirm the decline' }).getByRole('button', { name: 'Yes, decline' }).click()
  await stepUp(page, secret)
  await expect(page.getByRole('row').filter({ hasText: declined })).toHaveCount(0)
  await page.getByRole('radio', { name: 'Declined' }).click()
  await expect(page.getByRole('row').filter({ hasText: declined })).toBeVisible()
  await page.getByRole('radio', { name: 'Pending' }).click()

  // Approve the other with a tier; the code is shown once.
  await row.getByRole('button', { name: 'Approve' }).click()
  const form = page.getByRole('form', { name: 'Approve this request' })
  await expect(form.getByRole('option', { name: 'Individual' })).toBeAttached()
  await form.getByLabel('Tier').selectOption('instructor')
  await form.getByLabel('Days the tier lasts').fill('30')
  await form.getByRole('button', { name: 'Approve and create code' }).click()
  await stepUp(page, secret)
  const panel = page.getByTestId('one-time-code')
  await expect(panel).toBeVisible()
  const code = (await panel.locator('.font-mono').textContent())!.trim()
  expect(code.length).toBeGreaterThan(10)
  await panel.getByRole('button', { name: 'Done' }).click()
  await expect(page.getByTestId('one-time-code')).toHaveCount(0)
  await page.getByRole('radio', { name: 'Issued' }).click()
  await expect(page.getByRole('row').filter({ hasText: email })).toContainText('instructor for 30 days')
  await page.reload()
  await expect(page.locator('body')).not.toContainText(code)

  // The visitor signs up with the code and starts on that tier.
  await signUpThroughUi(visitor, email, PASSWORD, code)
  await signInThroughUi(visitor, email)
  const plan = await visitor.request.get('/api/account/plan')
  expect(plan.status()).toBe(200)
  expect(await plan.json()).toMatchObject({ plan: 'instructor', source: 'code' })

  await admin.context.close()
  await visitorContext.close()
})
