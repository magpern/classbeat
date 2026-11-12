import { expect, type Page } from '@playwright/test'
import { passCaptcha } from './helpers'

// Shared by invite-request.spec.ts and invite-request-a11y.spec.ts (PB-8, #986, ADR 0033).

/** The visitor's side: from the sign-up screen's link, fill the form and send it. */
export async function requestInviteThroughUi(
  page: Page,
  email: string,
  heardFrom = 'a friend at the studio',
  instructor: 'Yes' | 'No' = 'Yes',
): Promise<void> {
  await page.goto('/signup')
  await page.getByRole('link', { name: 'No invite code? Request an invite' }).click()
  await expect(page.getByRole('heading', { name: 'Request an invite' })).toBeVisible()
  await page.getByLabel('Email', { exact: true }).fill(email)
  await page.getByLabel('Where did you hear about classbeat?').fill(heardFrom)
  await page.getByRole('radio', { name: instructor }).click()
  await passCaptcha(page)
  await page.getByRole('button', { name: 'Request an invite' }).click()
  await expect(
    page.getByText('Thank you. If we can invite you, the invite code will be sent to this address.'),
  ).toBeVisible()
}
