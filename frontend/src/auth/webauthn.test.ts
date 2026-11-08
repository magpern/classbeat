import { afterEach, describe, expect, it, vi } from 'vitest'
import { CREATION_OPTIONS, REQUEST_OPTIONS, credential, error, installPasskeys, uninstallPasskeys } from '../test/passkeyMock'
import {
  PasskeyBrowserError,
  PasskeyCancelled,
  conditionalAvailable,
  createCredential,
  fromBase64Url,
  getCredential,
  parseOptions,
  passkeysSupported,
  signalCurrentUserDetails,
  signalUnknownCredential,
  toBase64Url,
} from './webauthn'
import { PromptCancelled } from './prompts'

afterEach(uninstallPasskeys)

describe('feature detection', () => {
  it('is off without PublicKeyCredential', () => {
    expect(passkeysSupported()).toBe(false)
  })
  it('is on with PublicKeyCredential and navigator.credentials', () => {
    installPasskeys()
    expect(passkeysSupported()).toBe(true)
  })
  it('is off when navigator.credentials has no create or get', () => {
    installPasskeys()
    Object.defineProperty(navigator, 'credentials', { value: {}, configurable: true })
    expect(passkeysSupported()).toBe(false)
  })
  it('asks the browser about conditional mediation, and says no when it cannot', async () => {
    expect(await conditionalAvailable()).toBe(false)
    installPasskeys({ conditional: true })
    expect(await conditionalAvailable()).toBe(true)
    installPasskeys({ conditional: false })
    expect(await conditionalAvailable()).toBe(false)
  })
})

describe('base64url', () => {
  it('round-trips bytes without padding', () => {
    const text = toBase64Url(new Uint8Array([251, 255, 254, 1]))
    expect(text).toBe('-__-AQ')
    expect(new Uint8Array(fromBase64Url(text))).toEqual(new Uint8Array([251, 255, 254, 1]))
  })
  it('turns the options of a registration and of a sign-in into buffers', () => {
    const creation = parseOptions(CREATION_OPTIONS, true) as Record<string, unknown>
    expect(creation.challenge).toBeInstanceOf(ArrayBuffer)
    expect((creation.user as { id: unknown }).id).toBeInstanceOf(ArrayBuffer)
    expect((creation.excludeCredentials as { id: unknown }[])[0]?.id).toBeInstanceOf(ArrayBuffer)
    const request = parseOptions({ ...REQUEST_OPTIONS, allowCredentials: [{ type: 'public-key', id: 'ZXhj' }] }, false) as Record<string, unknown>
    expect((request.allowCredentials as { id: unknown }[])[0]?.id).toBeInstanceOf(ArrayBuffer)
  })
})

describe('ceremonies', () => {
  it('reports cross-platform only for the exact string', async () => {
    for (const [attachment, expected] of [
      ['cross-platform', true],
      ['platform', false],
      ['Cross-Platform', false],
      ['cross-platform ', false],
      [null, false],
    ] as const) {
      installPasskeys({ result: credential(attachment) })
      expect((await getCredential(REQUEST_OPTIONS)).crossPlatform).toBe(expected)
    }
  })

  it('uses toJSON where the browser has it and builds the JSON by hand where it does not', async () => {
    installPasskeys({ result: credential('platform', true) })
    expect((await getCredential(REQUEST_OPTIONS)).json).toMatchObject({ id: 'cred-1', rawId: 'AQID' })
    installPasskeys({ result: credential('cross-platform', false) })
    const manual = (await getCredential(REQUEST_OPTIONS)).json
    expect(manual).toMatchObject({
      id: 'cred-1',
      rawId: 'AQID',
      type: 'public-key',
      authenticatorAttachment: 'cross-platform',
      response: { clientDataJSON: 'e30' },
    })
  })

  it('uses the browser parser for the options where there is one', async () => {
    const installed = installPasskeys({ result: credential() })
    const parse = vi.fn(() => ({ parsed: true }))
    Object.assign(window.PublicKeyCredential, { parseRequestOptionsFromJSON: parse })
    await getCredential(REQUEST_OPTIONS)
    expect(parse).toHaveBeenCalledWith(REQUEST_OPTIONS)
    expect(installed.get).toHaveBeenCalledWith({ publicKey: { parsed: true } })
  })

  it('passes the conditional mediation and the abort signal on', async () => {
    const installed = installPasskeys({ result: credential() })
    const controller = new AbortController()
    await getCredential(REQUEST_OPTIONS, { mediation: 'conditional', signal: controller.signal })
    expect(installed.get.mock.calls[0]?.[0]).toMatchObject({ mediation: 'conditional', signal: controller.signal })
  })

  it('treats a closed dialog, an abort and a null answer as a cancel the actions ignore', async () => {
    for (const result of [error('NotAllowedError'), error('AbortError')]) {
      installPasskeys({ result })
      const failed = await createCredential(CREATION_OPTIONS).catch((e: unknown) => e)
      expect(failed).toBeInstanceOf(PasskeyCancelled)
      expect(failed).toBeInstanceOf(PromptCancelled)
    }
    installPasskeys()
    Object.defineProperty(navigator, 'credentials', { value: { create: () => Promise.resolve(null), get: vi.fn() }, configurable: true })
    await expect(createCredential(CREATION_OPTIONS)).rejects.toBeInstanceOf(PasskeyCancelled)
  })

  it('names an already registered authenticator, an unsupported one and any other failure', async () => {
    const kinds: [string, string][] = [
      ['InvalidStateError', 'exists'],
      ['NotSupportedError', 'unsupported'],
      ['SecurityError', 'unsupported'],
      ['UnknownError', 'failed'],
    ]
    for (const [name, kind] of kinds) {
      installPasskeys({ result: error(name) })
      const failed = await createCredential(CREATION_OPTIONS).catch((e: unknown) => e)
      expect(failed).toBeInstanceOf(PasskeyBrowserError)
      expect((failed as PasskeyBrowserError).kind).toBe(kind)
    }
  })
})

describe('signals', () => {
  it('call the browser where it has them, and do nothing where it has not', async () => {
    await expect(signalUnknownCredential('classbeat.test', 'cred-1')).resolves.toBeUndefined()
    const none = installPasskeys({ signals: false })
    await signalUnknownCredential('classbeat.test', 'cred-1')
    expect(none.signalUnknownCredential).not.toHaveBeenCalled()
    const some = installPasskeys({ signals: true })
    await signalUnknownCredential('classbeat.test', 'cred-1')
    expect(some.signalUnknownCredential).toHaveBeenCalledWith({ rpId: 'classbeat.test', credentialId: 'cred-1' })
    const details = { rpId: 'classbeat.test', userId: 'dXNlcg', name: 'a', displayName: 'a' }
    await signalCurrentUserDetails(details)
    expect(some.signalCurrentUserDetails).toHaveBeenCalledWith(details)
  })

  it('never throw when the browser refuses', async () => {
    const some = installPasskeys({ signals: true })
    some.signalUnknownCredential.mockRejectedValueOnce(new Error('no'))
    await expect(signalUnknownCredential('classbeat.test', 'x')).resolves.toBeUndefined()
  })
})
