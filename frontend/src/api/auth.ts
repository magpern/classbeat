import { clearSubmittedInputs } from '../lib/submittedInput'
import { del, fetchSessionJson, get, post } from './client'
import {
  count,
  flag,
  list,
  object,
  optionalText,
  strings,
  text,
  type JsonObject,
} from './decode'

// Endpoints of ADR 0003 (backend/src/classbeat/auth/*_routes.py). Every answer is decoded
// and checked here, so pages only see typed values.

export interface SessionUser {
  id: string
  email: string
  /** `admin` only while the session has passed TOTP within 12 hours. */
  role: string
}

export interface SessionDetails {
  expiresAt: string
  recentAuth: boolean
  mfa: boolean
  stepup: boolean
  /** An admin account whose TOTP check is missing or lapsed: `role` is `user` until it passes. */
  adminPendingMfa: boolean
}

export interface SessionState {
  authenticated: boolean
  user: SessionUser | null
  session: SessionDetails | null
}

export async function getSession(signal?: AbortSignal): Promise<SessionState> {
  const body = await fetchSessionJson(signal)
  const authenticated = flag(body, 'authenticated')
  if (!authenticated) return { authenticated, user: null, session: null }
  const user = object(body.user, 'user')
  const session = object(body.session, 'session')
  return {
    authenticated,
    user: { id: text(user, 'id'), email: text(user, 'email'), role: text(user, 'role') },
    session: {
      expiresAt: text(session, 'expires_at'),
      recentAuth: flag(session, 'recent_auth'),
      mfa: flag(session, 'mfa'),
      stepup: flag(session, 'stepup'),
      adminPendingMfa: flag(session, 'admin_pending_mfa'),
    },
  }
}

export async function signUp(input: {
  email: string
  password: string
  inviteCode: string
}): Promise<void> {
  await post('/api/auth/signup', {
    email: input.email,
    password: input.password,
    invite_code: input.inviteCode === '' ? null : input.inviteCode,
  })
}

export async function confirmSignUp(token: string, password: string | null): Promise<void> {
  await post('/api/auth/signup/confirm', { token, password })
}

export async function login(email: string, password: string): Promise<{ role: string }> {
  const body = object(await post('/api/auth/login', { email, password }), 'login')
  return { role: text(body, 'role') }
}

export async function logout(): Promise<void> {
  await post('/api/auth/logout')
  clearSubmittedInputs()
}

export async function logoutAll(): Promise<void> {
  await post('/api/auth/logout-all')
  clearSubmittedInputs()
}

export async function reauthWithPassword(password: string): Promise<void> {
  await post('/api/auth/reauth', { password })
}

/** Admins only (ADR 0003 section 6): one code is a re-authentication and a step-up. */
export async function reauthWithTotp(code: string): Promise<void> {
  await post('/api/auth/reauth/totp', { code })
}

// --- password reset and change ---------------------------------------------------------

export async function requestPasswordReset(email: string): Promise<void> {
  await post('/api/auth/password/reset/request', { email })
}

export interface LinkedMethod {
  id: string
  provider: string
}

export async function resetInfo(token: string): Promise<LinkedMethod[]> {
  const body = object(await post('/api/auth/password/reset/info', { token }), 'reset info')
  return list(body, 'methods').map((row) => {
    const item = object(row, 'method')
    return { id: text(item, 'id'), provider: text(item, 'provider') }
  })
}

export async function completeReset(
  token: string,
  password: string,
  removeMethods: string[],
): Promise<void> {
  await post('/api/auth/password/reset/complete', {
    token,
    password,
    remove_methods: removeMethods,
  })
}

export async function changePassword(newPassword: string): Promise<void> {
  await post('/api/auth/password/change', { new_password: newPassword })
}

// --- email -----------------------------------------------------------------------------

export async function requestEmailChange(newEmail: string): Promise<void> {
  await post('/api/auth/email/change', { new_email: newEmail })
}

export async function confirmEmailChange(token: string): Promise<string> {
  const body = object(await post('/api/auth/email/confirm', { token }), 'email change')
  return text(body, 'email')
}

// --- linked sign-in methods ------------------------------------------------------------

export interface Identity {
  id: string
  provider: string
  createdAt: string
}

export interface IdentitiesState {
  identities: Identity[]
  hasPassword: boolean
  /** Providers that are switched on, whether linked or not. */
  available: string[]
}

export async function getIdentities(signal?: AbortSignal): Promise<IdentitiesState> {
  const body = object(await get('/api/auth/identities', signal), 'identities')
  return {
    identities: list(body, 'identities').map((row) => {
      const item = object(row, 'identity')
      return {
        id: text(item, 'id'),
        provider: text(item, 'provider'),
        createdAt: text(item, 'created_at'),
      }
    }),
    hasPassword: flag(body, 'has_password'),
    available: strings(body, 'available'),
  }
}

export async function unlinkIdentity(id: string): Promise<void> {
  await del(`/api/auth/identities/${encodeURIComponent(id)}`)
}

// --- OAuth -----------------------------------------------------------------------------

export async function getOAuthProviders(signal?: AbortSignal): Promise<string[]> {
  const body = object(await get('/api/auth/oauth/providers', signal), 'providers')
  return strings(body, 'providers')
}

export type OAuthPurpose = 'sign_in' | 'link' | 'reauth'

/** The provider's authorization URL; the caller sends the browser there. */
export async function startOAuth(
  provider: string,
  purpose: OAuthPurpose,
  extra: { inviteCode?: string; returnTo?: string } = {},
): Promise<string> {
  const body = object(
    await post(`/api/auth/oauth/${encodeURIComponent(provider)}/start`, {
      purpose,
      invite_code: extra.inviteCode ? extra.inviteCode : null,
      return_to: extra.returnTo ?? null,
    }),
    'oauth start',
  )
  return text(body, 'authorization_url')
}

export interface PendingSignup {
  pending: boolean
  provider: string | null
  awaitingEmail: boolean
}

export async function getPendingSignup(signal?: AbortSignal): Promise<PendingSignup> {
  const body = object(await get('/api/auth/oauth/pending', signal), 'pending')
  return {
    pending: flag(body, 'pending'),
    provider: optionalText(body, 'provider'),
    awaitingEmail: body.awaiting_email === true,
  }
}

export async function submitPendingEmail(email: string): Promise<void> {
  await post('/api/auth/oauth/pending/email', { email })
}

// --- two-factor ------------------------------------------------------------------------

export interface MfaStatus {
  totpEnabled: boolean
  recoveryCodesLeft: number
}

export async function getMfaStatus(signal?: AbortSignal): Promise<MfaStatus> {
  const body = object(await get('/api/auth/mfa', signal), 'mfa')
  return {
    totpEnabled: flag(body, 'totp_enabled'),
    recoveryCodesLeft: count(body, 'recovery_codes_left'),
  }
}

export interface Enrollment {
  secret: string
  qrSvg: string
  pending: string
}

export async function startTotp(): Promise<Enrollment> {
  const body = object(await post('/api/auth/mfa/totp/start'), 'enrollment')
  return {
    secret: text(body, 'secret'),
    qrSvg: text(body, 'qr_svg'),
    pending: text(body, 'pending'),
  }
}

function recoveryCodes(body: JsonObject): string[] {
  return strings(body, 'recovery_codes')
}

export async function confirmTotp(pending: string, code: string): Promise<string[]> {
  const body = object(await post('/api/auth/mfa/totp/confirm', { pending, code }), 'confirm')
  return recoveryCodes(body)
}

export async function removeTotp(): Promise<void> {
  await del('/api/auth/mfa/totp')
}

export async function regenerateRecoveryCodes(): Promise<string[]> {
  return recoveryCodes(object(await post('/api/auth/mfa/recovery-codes'), 'recovery codes'))
}

/** The second step after sign-in: a TOTP or recovery code. */
export async function verifyMfa(code: string): Promise<void> {
  await post('/api/auth/mfa/verify', { code })
}

/** A TOTP or recovery code, good for five minutes. */
export async function stepUp(code: string): Promise<void> {
  await post('/api/auth/mfa/stepup', { code })
}

export async function adminWhoami(signal?: AbortSignal): Promise<string> {
  return text(object(await get('/api/admin/whoami', signal), 'whoami'), 'email')
}

export interface DeviceSession {
  id: string
  createdAt: string
  lastSeenAt: string
  userAgent: string | null
  current: boolean
}

/** `GET /api/auth/sessions`: the user's own signed-in devices. The IP address is not kept here. */
export async function listDevices(signal?: AbortSignal): Promise<DeviceSession[]> {
  const body = await get('/api/auth/sessions', signal)
  if (!Array.isArray(body)) throw new Error('Unexpected response from the server (sessions).')
  return body.map((item: unknown) => {
    const row = object(item, 'session')
    return {
      id: text(row, 'id'),
      createdAt: text(row, 'created_at'),
      lastSeenAt: text(row, 'last_seen_at'),
      userAgent: optionalText(row, 'user_agent'),
      current: flag(row, 'current'),
    }
  })
}

/** Signs out one of the user's own devices (`DELETE /api/auth/sessions/{id}`). */
export async function signOutDevice(id: string): Promise<void> {
  await del(`/api/auth/sessions/${encodeURIComponent(id)}`)
}

/**
 * Deletes the account (`DELETE /api/auth/account`, ADR 0003 section 6): recent authentication,
 * and a step-up once TOTP is set up; the caller runs it through the guard.
 */
export async function deleteAccount(): Promise<void> {
  await del('/api/auth/account')
  clearSubmittedInputs()
}
