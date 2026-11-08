import { PromptCancelled } from './prompts'

// The browser half of passkeys (ADR 0019): feature detection, the JSON <-> binary conversion
// and the ceremonies. No network here and nothing is stored; `passkeyCeremony.ts` joins this
// to the API. Every call is feature-detected, so a browser without WebAuthn sees no passkey UI.

type Json = Record<string, unknown>

/** The user closed the browser's dialog, or the ceremony was aborted: not an error to show. */
export class PasskeyCancelled extends PromptCancelled {}

/** The browser refused for a reason the page can word: `exists` is a passkey already on this device. */
export class PasskeyBrowserError extends Error {
  readonly kind: 'exists' | 'unsupported' | 'failed'
  constructor(kind: 'exists' | 'unsupported' | 'failed') {
    super(kind)
    this.name = 'PasskeyBrowserError'
    this.kind = kind
  }
}

export interface CeremonyResult {
  json: Json
  /** Only the exact string "cross-platform" (a phone or a security key), never a guess (owner question 4). */
  crossPlatform: boolean
}

/** The whole passkey UI hides without this. */
export function passkeysSupported(): boolean {
  return (
    typeof window !== 'undefined' &&
    typeof window.PublicKeyCredential === 'function' &&
    typeof navigator !== 'undefined' &&
    typeof navigator.credentials?.create === 'function' &&
    typeof navigator.credentials?.get === 'function'
  )
}

/** Whether the browser can fill passkeys into the email field (conditional mediation). */
export async function conditionalAvailable(): Promise<boolean> {
  if (!passkeysSupported()) return false
  try {
    const check = (window.PublicKeyCredential as unknown as { isConditionalMediationAvailable?: () => Promise<boolean> })
      .isConditionalMediationAvailable
    return typeof check === 'function' ? (await check.call(window.PublicKeyCredential)) === true : false
  } catch {
    return false
  }
}

export function fromBase64Url(value: string): ArrayBuffer {
  const padded = value.replace(/-/g, '+').replace(/_/g, '/').padEnd(Math.ceil(value.length / 4) * 4, '=')
  const raw = atob(padded)
  const bytes = new Uint8Array(raw.length)
  for (let i = 0; i < raw.length; i += 1) bytes[i] = raw.charCodeAt(i)
  return bytes.buffer
}

export function toBase64Url(buffer: ArrayBuffer | ArrayBufferView): string {
  const view = ArrayBuffer.isView(buffer)
    ? new Uint8Array(buffer.buffer, buffer.byteOffset, buffer.byteLength)
    : new Uint8Array(buffer)
  let raw = ''
  for (const byte of view) raw += String.fromCharCode(byte)
  return btoa(raw).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

function idOf(value: unknown): Json {
  return { ...(value as Json), id: fromBase64Url(String((value as Json).id)) }
}

/** The manual fallback of `PublicKeyCredential.parse*OptionsFromJSON`: base64url strings to buffers. */
export function parseOptions(options: Json, creation: boolean): Json {
  const out: Json = { ...options, challenge: fromBase64Url(String(options.challenge)) }
  if (creation) {
    out.user = idOf(options.user)
    if (Array.isArray(options.excludeCredentials)) out.excludeCredentials = options.excludeCredentials.map(idOf)
  } else if (Array.isArray(options.allowCredentials)) {
    out.allowCredentials = options.allowCredentials.map(idOf)
  }
  return out
}

type Parser = (json: Json) => unknown

function nativeParser(name: 'parseCreationOptionsFromJSON' | 'parseRequestOptionsFromJSON'): Parser | null {
  const fn = (window.PublicKeyCredential as unknown as Record<string, unknown>)[name]
  return typeof fn === 'function' ? (json) => (fn as Parser).call(window.PublicKeyCredential, json) : null
}

function bufferOf(value: unknown): string | null {
  return value instanceof ArrayBuffer || ArrayBuffer.isView(value) ? toBase64Url(value) : null
}

/** The manual fallback of `credential.toJSON()` for a registration or an assertion. */
function manualJson(credential: PublicKeyCredential): Json {
  const response = credential.response as unknown as Record<string, unknown>
  const out: Json = {}
  for (const key of ['clientDataJSON', 'attestationObject', 'authenticatorData', 'signature', 'userHandle']) {
    const encoded = bufferOf(response[key])
    if (encoded !== null) out[key] = encoded
  }
  const transports = (response as { getTransports?: () => string[] }).getTransports
  if (typeof transports === 'function') out.transports = transports.call(response)
  const attachment = credential.authenticatorAttachment
  return {
    id: credential.id,
    rawId: toBase64Url(credential.rawId),
    type: credential.type,
    response: out,
    authenticatorAttachment: attachment ?? undefined,
    clientExtensionResults: credential.getClientExtensionResults?.() ?? {},
  }
}

function resultOf(credential: Credential | null): CeremonyResult {
  if (credential === null) throw new PasskeyCancelled()
  const key = credential as PublicKeyCredential
  const toJSON = (key as unknown as { toJSON?: () => Json }).toJSON
  const json = typeof toJSON === 'function' ? toJSON.call(key) : manualJson(key)
  return { json, crossPlatform: key.authenticatorAttachment === 'cross-platform' }
}

function translate(error: unknown): never {
  // Duck-typed: a DOMException from another realm is not `instanceof` this one.
  const raw = typeof error === 'object' && error !== null ? (error as { name?: unknown }).name : undefined
  const name = typeof raw === 'string' ? raw : ''
  if (name === 'NotAllowedError' || name === 'AbortError') throw new PasskeyCancelled()
  if (name === 'InvalidStateError') throw new PasskeyBrowserError('exists')
  if (name === 'NotSupportedError' || name === 'SecurityError') throw new PasskeyBrowserError('unsupported')
  throw new PasskeyBrowserError('failed')
}

export async function createCredential(options: Json): Promise<CeremonyResult> {
  const parse = nativeParser('parseCreationOptionsFromJSON')
  const publicKey = (parse ? parse(options) : parseOptions(options, true)) as PublicKeyCredentialCreationOptions
  try {
    return resultOf(await navigator.credentials.create({ publicKey }))
  } catch (error) {
    if (error instanceof PasskeyCancelled) throw error
    return translate(error)
  }
}

export interface GetOptions {
  /** `conditional`: the browser offers passkeys in the email field's autofill; it waits for the user. */
  mediation?: 'conditional'
  signal?: AbortSignal
}

export async function getCredential(options: Json, extra: GetOptions = {}): Promise<CeremonyResult> {
  const parse = nativeParser('parseRequestOptionsFromJSON')
  const publicKey = (parse ? parse(options) : parseOptions(options, false)) as PublicKeyCredentialRequestOptions
  try {
    return resultOf(
      await navigator.credentials.get({
        publicKey,
        ...(extra.mediation ? { mediation: extra.mediation } : {}),
        ...(extra.signal ? { signal: extra.signal } : {}),
      }),
    )
  } catch (error) {
    if (error instanceof PasskeyCancelled) throw error
    return translate(error)
  }
}

async function signal(name: 'signalUnknownCredential' | 'signalCurrentUserDetails', details: Json): Promise<void> {
  if (!passkeysSupported()) return
  const fn = (window.PublicKeyCredential as unknown as Record<string, unknown>)[name]
  if (typeof fn !== 'function') return
  try {
    await (fn as (d: Json) => Promise<void>).call(window.PublicKeyCredential, details)
  } catch {
    // A hint to the passkey manager; a refusal changes nothing for the user.
  }
}

/** Tells the passkey manager a credential the server does not know can go (`passkey_unknown`). */
export function signalUnknownCredential(rpId: string, credentialId: string): Promise<void> {
  return signal('signalUnknownCredential', { rpId, credentialId })
}

/** Keeps the passkey manager's name and display name for a user handle current. */
export function signalCurrentUserDetails(details: {
  rpId: string
  userId: string
  name: string
  displayName: string
}): Promise<void> {
  return signal('signalCurrentUserDetails', details)
}
