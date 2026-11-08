import { ApiError } from '../api/client'
import { errorMessage } from '../api/messages'
import {
  reauthFinish,
  reauthStart,
  registerFinish,
  registerStart,
  signinFinish,
  signinStart,
  type Passkey,
} from '../api/passkeys'
import { t } from '../i18n/t'
import {
  createCredential,
  getCredential,
  PasskeyBrowserError,
  signalCurrentUserDetails,
  signalUnknownCredential,
  type CeremonyResult,
} from './webauthn'

// The three ceremonies of ADR 0019, each from the server's options to the server's verdict.
// The browser's dialog is the only place a user proves anything; a cancel is a PasskeyCancelled.

/** Adds a passkey to the signed-in account. Run it through `guard()`: it needs recent auth and step-up. */
export async function registerPasskey(label: string | undefined): Promise<Passkey> {
  const options = await registerStart()
  const created = await createCredential(options)
  const passkey = await registerFinish(created.json, label)
  const rp = options.rp as { id?: string } | undefined
  const user = options.user as { id?: string; name?: string; displayName?: string } | undefined
  if (rp?.id && user?.id && user.name) {
    // Keeps the passkey manager's entry named like the account (a hint only).
    void signalCurrentUserDetails({
      rpId: rp.id,
      userId: user.id,
      name: user.name,
      displayName: user.displayName ?? user.name,
    })
  }
  return passkey
}

export interface SignInOutcome {
  role: string
  crossPlatform: boolean
}

/**
 * Signs in with a passkey. `conditional` offers it through the email field's autofill and waits
 * for the user; `signal` stops that wait. A credential the server does not know (`passkey_unknown`)
 * is reported to the passkey manager so it can drop the entry, then the error goes on.
 */
export async function signInWithPasskey(
  mode: { conditional: boolean; signal?: AbortSignal } = { conditional: false },
): Promise<SignInOutcome> {
  const options = await signinStart()
  const got = await getCredential(options, {
    ...(mode.conditional ? { mediation: 'conditional' as const } : {}),
    ...(mode.signal ? { signal: mode.signal } : {}),
  })
  try {
    const { role } = await signinFinish(got.json)
    return { role, crossPlatform: got.crossPlatform }
  } catch (error) {
    await reportUnknown(error, options, got)
    throw error
  }
}

async function reportUnknown(error: unknown, options: Record<string, unknown>, got: CeremonyResult): Promise<void> {
  if (!(error instanceof ApiError) || error.reason !== 'passkey_unknown') return
  const rpId = typeof options.rpId === 'string' ? options.rpId : null
  const id = typeof got.json.id === 'string' ? got.json.id : null
  if (rpId !== null && id !== null) await signalUnknownCredential(rpId, id)
}

/** Proves recent authentication with a passkey, for a signed-in user. */
export async function reauthWithPasskey(): Promise<void> {
  const got = await getCredential(await reauthStart())
  await reauthFinish(got.json)
}

/** The sentence for a failed ceremony: the browser's own failures, else the API's reasons. */
export function passkeyErrorMessage(error: unknown): string {
  if (error instanceof PasskeyBrowserError) {
    if (error.kind === 'exists') return t('passkey.error.exists')
    if (error.kind === 'unsupported') return t('passkey.error.unsupported')
    return t('passkey.error.failed')
  }
  return errorMessage(error)
}
