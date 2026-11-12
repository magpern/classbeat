import { expect, test, uniqueEmail } from './helpers'
import { requestInviteThroughUi } from './invite-request-helpers'
import { enrollTotp, newUser } from './m5-helpers'
import { audit, stackContext } from './mu-helpers'

// PB-8 (#986): the request form and the admin Invite requests screen (list, and with the approve
// form open) pass the accessibility audit at 390 and 1440 px in both themes: no sideways scroll,
// the primary action in the window, no serious or critical axe violation. Nothing is approved or
// declined here, so the shared stack only gains one pending request.
test.setTimeout(300_000)

test('the request form and the admin screen pass the audit', async ({ browser, baseURL }) => {
  const visitorContext = await stackContext(browser, baseURL!)
  const visitor = await visitorContext.newPage()
  await visitor.goto('/signup')
  await visitor.getByRole('link', { name: 'No invite code? Request an invite' }).click()
  await expect(visitor.getByRole('heading', { name: 'Request an invite' })).toBeVisible()
  const form = await audit(visitor, 'Request an invite', visitor.getByRole('button', { name: 'Request an invite' }))
  expect(form.problems).toEqual([])

  const email = uniqueEmail('a11y-request')
  await requestInviteThroughUi(visitor, email)
  const sent = await audit(visitor, 'Request sent', visitor.getByRole('link', { name: 'Back to sign up' }))
  expect(sent.problems).toEqual([])

  const admin = await newUser(browser, baseURL, 'invite-req-a11y', false, true)
  await enrollTotp(admin.page)
  const page = admin.page
  await page.goto('/admin/invite-requests')
  await expect(page.getByRole('row').filter({ hasText: email })).toBeVisible()
  const list = await audit(page, 'Admin invite requests', page.getByRole('radio', { name: 'Pending' }))
  expect(list.problems).toEqual([])

  await page.getByRole('row').filter({ hasText: email }).getByRole('button', { name: 'Approve' }).click()
  await expect(page.getByRole('form', { name: 'Approve this request' })).toBeVisible()
  const approve = await audit(
    page,
    'Admin approve form',
    page.getByRole('button', { name: 'Approve and create code' }),
  )
  expect(approve.problems).toEqual([])

  await admin.context.close()
  await visitorContext.close()
})
