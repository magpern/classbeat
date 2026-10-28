import { execFileSync } from 'node:child_process'
import { expect, test, type APIRequestContext, type APIResponse } from '@playwright/test'

// Compose-level check of ADR 0003 section 5: sign-up, confirmation and password reset by mail.
// The mails are read from mailpit's API; nothing leaves the stack. API only (the pages come
// with the frontend issue). Cookies are handled by hand because the __Host- cookies are Secure.

const mailpit = `http://127.0.0.1:${process.env.MAILPIT_PORT ?? '8025'}`
const PASSWORD = 'correct horse battery staple'
const NEW_PASSWORD = 'a brand new passphrase!'

class Jar {
  cookies = new Map<string, string>()

  absorb(response: APIResponse): void {
    for (const header of response.headersArray().filter((h) => h.name.toLowerCase() === 'set-cookie')) {
      const [pair] = header.value.split(';')
      const index = pair.indexOf('=')
      this.cookies.set(pair.slice(0, index), pair.slice(index + 1))
    }
  }

  header(): string {
    return [...this.cookies].map(([k, v]) => `${k}=${v}`).join('; ')
  }
}

async function post(
  request: APIRequestContext,
  origin: string,
  jar: Jar,
  path: string,
  data: object,
): Promise<APIResponse> {
  const session = await request.get('/api/auth/session', { headers: { Cookie: jar.header() } })
  jar.absorb(session)
  const { csrf_token: csrf } = (await session.json()) as { csrf_token: string }
  const response = await request.post(path, {
    data,
    headers: { Origin: origin, 'X-CSRF-Token': csrf, Cookie: jar.header() },
  })
  jar.absorb(response)
  return response
}

async function tokenFromMail(request: APIRequestContext, to: string, subject: string): Promise<string> {
  for (let attempt = 0; attempt < 30; attempt++) {
    const list = (await (await request.get(`${mailpit}/api/v1/messages`)).json()) as {
      messages: { ID: string; Subject: string; To: { Address: string }[] }[]
    }
    const found = list.messages.find((m) => m.Subject.includes(subject) && m.To.some((t) => t.Address === to))
    if (found) {
      const message = (await (await request.get(`${mailpit}/api/v1/message/${found.ID}`)).json()) as {
        Text: string
      }
      const match = /#token=([\w-]+)/.exec(message.Text)
      expect(match, 'the mail carries a token link').not.toBeNull()
      return match![1]
    }
    await new Promise((resolve) => setTimeout(resolve, 500))
  }
  throw new Error(`no "${subject}" mail for the test address arrived in mailpit`)
}

test('sign-up, confirmation, sign-in and password reset by mail', async ({ request, baseURL }) => {
  const origin = baseURL!
  const email = `e2e-${Date.now()}@example.test`
  const code = execFileSync('docker', ['compose', 'exec', '-T', 'web', 'python', '-m', 'classbeat', 'invite', 'create'], {
    cwd: new URL('../..', import.meta.url),
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'ignore'],
  }).trim()

  const browser = new Jar()
  const signUp = await post(request, origin, browser, '/api/auth/signup', {
    email,
    password: PASSWORD,
    invite_code: code,
  })
  expect(signUp.status()).toBe(202)

  const confirmToken = await tokenFromMail(request, email, 'Confirm your classbeat account')
  const wrong = await post(request, origin, new Jar(), '/api/auth/signup/confirm', {
    token: confirmToken,
    password: 'not the chosen password',
  })
  expect(wrong.status()).toBe(400)
  const confirm = await post(request, origin, new Jar(), '/api/auth/signup/confirm', {
    token: confirmToken,
    password: PASSWORD,
  })
  expect(confirm.status()).toBe(200)

  const signIn = await post(request, origin, new Jar(), '/api/auth/login', { email, password: PASSWORD })
  expect(signIn.status()).toBe(200)

  const reset = await post(request, origin, new Jar(), '/api/auth/password/reset/request', { email })
  expect(reset.status()).toBe(202)
  const resetToken = await tokenFromMail(request, email, 'Reset your classbeat password')
  const done = await post(request, origin, new Jar(), '/api/auth/password/reset/complete', {
    token: resetToken,
    password: NEW_PASSWORD,
    remove_methods: [],
  })
  expect(done.status()).toBe(200)
  const old = await post(request, origin, new Jar(), '/api/auth/login', { email, password: PASSWORD })
  expect(old.status()).toBe(401)
  const fresh = await post(request, origin, new Jar(), '/api/auth/login', { email, password: NEW_PASSWORD })
  expect(fresh.status()).toBe(200)
})
