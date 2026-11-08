import { del, get, post, request } from './client'
import { count, flag, list, object, optionalText, strings, text } from './decode'

// Endpoints of ADR 0019 (backend/src/classbeat/auth/passkey_*_routes.py). The credentials the
// browser makes travel as the `toJSON()` form of a PublicKeyCredential; the server verifies
// them. Nothing here ever holds a key: the list shows a label, dates and two facts.

export interface Passkey {
  id: string
  label: string
  transports: string[]
  /** Backup eligible: shown as "synced", else "this device only". */
  synced: boolean
  createdAt: string
  lastUsedAt: string | null
}

export interface PasskeyList {
  passkeys: Passkey[]
  /** CLASSBEAT_PASSKEYS. Off: the list can still be read and removed from. */
  enabled: boolean
  max: number
}

export type CredentialJson = Record<string, unknown>

function decodePasskey(value: unknown): Passkey {
  const row = object(value, 'passkey')
  return {
    id: text(row, 'id'),
    label: text(row, 'label'),
    transports: strings(row, 'transports'),
    synced: flag(row, 'synced'),
    createdAt: text(row, 'created_at'),
    lastUsedAt: optionalText(row, 'last_used_at'),
  }
}

export async function listPasskeys(signal?: AbortSignal): Promise<PasskeyList> {
  const body = object(await get('/api/auth/passkeys', signal), 'passkeys')
  return {
    passkeys: list(body, 'passkeys').map(decodePasskey),
    enabled: flag(body, 'enabled'),
    max: count(body, 'max'),
  }
}

export async function renamePasskey(id: string, label: string): Promise<Passkey> {
  const body = await request('PATCH', `/api/auth/passkeys/${encodeURIComponent(id)}`, { body: { label } })
  return decodePasskey(body)
}

export async function removePasskey(id: string): Promise<void> {
  await del(`/api/auth/passkeys/${encodeURIComponent(id)}`)
}

/** The `publicKey` options of a ceremony, in the JSON form the browser parses. */
async function options(path: string): Promise<CredentialJson> {
  const body = object(await post(path), 'passkey options')
  return object(body.publicKey, 'publicKey')
}

export const registerStart = (): Promise<CredentialJson> => options('/api/auth/passkeys/register/start')
export const signinStart = (): Promise<CredentialJson> => options('/api/auth/passkeys/signin/start')
export const reauthStart = (): Promise<CredentialJson> => options('/api/auth/passkeys/reauth/start')

export async function registerFinish(credential: CredentialJson, label?: string): Promise<Passkey> {
  const body = await post('/api/auth/passkeys/register/finish', {
    credential,
    ...(label ? { label } : {}),
  })
  return decodePasskey(body)
}

export async function signinFinish(credential: CredentialJson): Promise<{ role: string }> {
  const body = object(await post('/api/auth/passkeys/signin/finish', { credential }), 'sign-in')
  return { role: text(body, 'role') }
}

export async function reauthFinish(credential: CredentialJson): Promise<void> {
  await post('/api/auth/passkeys/reauth/finish', { credential })
}
