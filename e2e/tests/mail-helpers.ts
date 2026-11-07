import { expect, type APIRequestContext, type Browser } from '@playwright/test'
import { mailpit, passCaptcha, randomClientIp } from './helpers'

// Shared by the mail specs (mail-admin.spec.ts, mail-unsubscribe.spec.ts; ME, #649). Mail is read
// from mailpit's API only; the stack's SMTP goes to mailpit and nowhere else.

export interface Mail {
  id: string
  subject: string
  from: { name: string; address: string }
  replyTo: string[]
  text: string
  html: string
  headers: Record<string, string[]>
}

interface Summary {
  ID: string
  Subject: string
}

/** Every mail to this address, newest first (a search, so the list's page size does not hide it). */
async function summariesTo(request: APIRequestContext, to: string): Promise<Summary[]> {
  const query = encodeURIComponent(`to:"${to}"`)
  const answer = await request.get(`${mailpit}/api/v1/search?query=${query}&limit=200`)
  expect(answer.ok(), 'mailpit answers the search').toBe(true)
  return ((await answer.json()) as { messages: Summary[] }).messages
}

/** The mail with exactly this subject to this address (waits for it to arrive). */
export async function waitForMail(request: APIRequestContext, to: string, subject: string): Promise<Mail> {
  // `expect.poll` reports the last subjects seen when the mail never comes (same 20 s as before).
  const found: { summary?: Summary } = {}
  await expect
    .poll(
      async () => {
        const all = await summariesTo(request, to)
        found.summary = all.find((m) => m.Subject === subject)
        return found.summary ? subject : all.map((m) => m.Subject)
      },
      { message: `a "${subject}" mail for ${to} arrives in mailpit`, timeout: 20_000, intervals: [500] },
    )
    .toBe(subject)
  const id = found.summary?.ID
  if (id === undefined) throw new Error(`the "${subject}" mail for ${to} was not found`)
  const message = (await (await request.get(`${mailpit}/api/v1/message/${id}`)).json()) as {
    From: { Name: string; Address: string }
    ReplyTo: { Address: string }[] | null
    Subject: string
    Text: string
    HTML: string
  }
  const headers = (await (await request.get(`${mailpit}/api/v1/message/${id}/headers`)).json()) as Record<
    string,
    string[]
  >
  return {
    id,
    subject: message.Subject,
    from: { name: message.From.Name, address: message.From.Address },
    replyTo: (message.ReplyTo ?? []).map((a) => a.Address),
    text: message.Text,
    html: message.HTML,
    headers,
  }
}

/** How many mails with exactly this subject went to this address so far. */
export async function countMails(request: APIRequestContext, to: string, subject: string): Promise<number> {
  return (await summariesTo(request, to)).filter((m) => m.Subject === subject).length
}

/** The first `http(s)` link in the text part that holds this marker. */
export function linkWith(text: string, marker: string): string {
  const match = new RegExp(`https?://\\S*${marker}\\S*`).exec(text)
  expect(match, `the text part carries a link with ${marker}`).not.toBeNull()
  return match![0]
}

/** Asks for a password-reset mail for `email` from a browser of its own (an essential mail). */
export async function requestReset(browser: Browser, baseURL: string | undefined, email: string): Promise<void> {
  const context = await browser.newContext({
    baseURL,
    extraHTTPHeaders: { 'X-Forwarded-For': randomClientIp() },
  })
  const page = await context.newPage()
  await page.goto('/reset')
  await page.getByLabel('Email', { exact: true }).fill(email)
  await passCaptcha(page)
  await page.getByRole('button', { name: 'Send reset link' }).click()
  await expect(page.getByRole('heading', { name: 'Check your inbox' })).toBeVisible()
  await context.close()
}
