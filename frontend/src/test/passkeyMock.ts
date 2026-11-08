import { vi, type Mock } from 'vitest'

// A fake WebAuthn for component tests: `window.PublicKeyCredential` and `navigator.credentials`.
// Nothing real is touched. A conditional `get` waits until the page aborts it, like a browser.

export interface FakeCredential {
  id: string
  rawId: ArrayBuffer
  type: 'public-key'
  authenticatorAttachment: string | null
  response: Record<string, unknown>
  getClientExtensionResults: () => Record<string, unknown>
  toJSON?: () => Record<string, unknown>
}

export function credential(attachment: string | null = 'platform', withToJSON = true): FakeCredential {
  const json = {
    id: 'cred-1',
    rawId: 'AQID',
    type: 'public-key',
    authenticatorAttachment: attachment,
    response: { clientDataJSON: 'e30' },
  }
  return {
    id: 'cred-1',
    rawId: new Uint8Array([1, 2, 3]).buffer,
    type: 'public-key',
    authenticatorAttachment: attachment,
    response: { clientDataJSON: new Uint8Array([123, 125]).buffer },
    getClientExtensionResults: () => ({}),
    ...(withToJSON ? { toJSON: () => json } : {}),
  }
}

export const CREATION_OPTIONS = {
  rp: { id: 'classbeat.test', name: 'classbeat' },
  user: { id: 'dXNlcg', name: 'ann@example.test', displayName: 'ann@example.test' },
  challenge: 'Y2hhbGw',
  pubKeyCredParams: [{ type: 'public-key', alg: -7 }],
  excludeCredentials: [{ type: 'public-key', id: 'ZXhj' }],
}

export const REQUEST_OPTIONS = {
  rpId: 'classbeat.test',
  challenge: 'Y2hhbGw',
  userVerification: 'required',
  allowCredentials: [],
}

export interface Installed {
  create: ReturnType<typeof vi.fn>
  get: Mock<(init: { mediation?: string; signal?: AbortSignal }) => Promise<unknown>>
  signalUnknownCredential: ReturnType<typeof vi.fn>
  signalCurrentUserDetails: ReturnType<typeof vi.fn>
}

interface Options {
  conditional?: boolean
  signals?: boolean
  /** What a non-conditional `get` and `create` return; a conditional `get` waits to be aborted. */
  result?: FakeCredential | Error
}

function abortable(signal: AbortSignal | undefined): Promise<never> {
  return new Promise((_resolve, reject) => {
    signal?.addEventListener('abort', () => {
      reject(new DOMException('aborted', 'AbortError'))
    })
  })
}

export function installPasskeys(options: Options = {}): Installed {
  const result = options.result ?? credential()
  // A DOMException is not `instanceof Error` in jsdom: tell a failure from a credential by its shape.
  const answer = () => ('rawId' in result ? Promise.resolve(result) : Promise.reject(result))
  const create = vi.fn(() => answer())
  const get = vi.fn((init: { mediation?: string; signal?: AbortSignal }) =>
    init.mediation === 'conditional' && options.result === undefined ? abortable(init.signal) : answer(),
  )
  const signalUnknownCredential = vi.fn(() => Promise.resolve())
  const signalCurrentUserDetails = vi.fn(() => Promise.resolve())
  class FakePublicKeyCredential {
    static isConditionalMediationAvailable = () => Promise.resolve(options.conditional === true)
    static signalUnknownCredential = options.signals ? signalUnknownCredential : undefined
    static signalCurrentUserDetails = options.signals ? signalCurrentUserDetails : undefined
  }
  vi.stubGlobal('PublicKeyCredential', FakePublicKeyCredential)
  Object.defineProperty(navigator, 'credentials', { value: { create, get }, configurable: true })
  return { create, get, signalUnknownCredential, signalCurrentUserDetails }
}

export function uninstallPasskeys(): void {
  vi.unstubAllGlobals()
  Reflect.deleteProperty(navigator, 'credentials')
}

export function error(name: string): DOMException {
  return new DOMException(name, name)
}
