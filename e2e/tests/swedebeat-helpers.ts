import { inContainer } from './spotify-helpers'

// Shared by the Swedebeat spec: the fake Swedebeat's public test data and test control
// (e2e/fake-swedebeat/server.py). The fake is only on the compose network, so every control call
// is made from inside the web container. Nothing here prints a password or a token.

export const EMAIL_A = 'rider@example.test'
export const PASSWORD_A = 'e2e-fake-swedebeat-password'
export const EMAIL_B = 'second@example.test'
export const PASSWORD_B = 'e2e-fake-swedebeat-second'
export const PHONE = 'phone-device-0001'

const CONTROL = [
  'import sys, urllib.request as u',
  'u.install_opener(u.build_opener(u.ProxyHandler({})))',
  'print(u.urlopen("https://www.swedebeat.se/__e2e/" + sys.argv[1], timeout=15).read().decode())',
].join('\n')

function control<T = unknown>(path: string): T {
  return JSON.parse(inContainer('web', CONTROL, path)) as T
}

export interface FakeLogin {
  email: string
  device_id: string
  force: boolean
  ok: boolean
}

export interface FakeState {
  logins: FakeLogin[]
  calls: { path: string; email: string | null }[]
  outage: boolean
}

export const fakeState = (): FakeState => control<FakeState>('state')

/** Back to the seeded fake: two accounts, nothing held, no outage. */
export const resetSwedebeat = (): void => {
  control('reset')
}

/** Another device (the phone app) takes the account's Swedebeat session. */
export const holdSession = (email: string, device = PHONE): void => {
  control(`hold?email=${encodeURIComponent(email)}&device=${encodeURIComponent(device)}`)
}

export const setOutage = (on: boolean): void => {
  control(`outage?on=${on ? '1' : '0'}`)
}
