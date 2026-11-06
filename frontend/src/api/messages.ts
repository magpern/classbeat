import type { CatalogueKey } from '../i18n/en'
import { t } from '../i18n/t'
import { ApiError } from './client'

// Plain-language texts for the `reason` codes of the auth API, worded by the catalogue (`error.reason.<code>`).
// Unknown codes get a generic message; the raw code is never shown.
const REASON_CODES = [
  'confirmation_mismatch',
  'invalid_invite',
  'invite_invalid',
  'signups_closed',
  'password_rule',
  'invalid_credentials',
  'wrong_password',
  'invalid_token',
  'wrong_browser',
  'account_exists',
  'email_taken',
  'last_sign_in_method',
  'invalid_code',
  'totp_changed',
  'totp_not_enabled',
  'not_authenticated',
  'no_pending_signup',
  'provider_disabled',
  'forbidden',
  'busy',
] as const

function reasonKey(reason: string): CatalogueKey | undefined {
  return (REASON_CODES as readonly string[]).includes(reason)
    ? (`error.reason.${reason}` as CatalogueKey)
    : undefined
}

// Outcome codes the OAuth callback puts in `?oauth=` of /signin and /account.
// (`reauth_stale` and `no_account` are worded below, with their variants.)
const OAUTH_OUTCOME_CODES = [
  'invalid',
  'denied',
  'session_mismatch',
  'account_exists',
  // Only a sign-up flow gets this (it lands on /signup, whose invite field is below the box).
  'invite_required',
  'invite_invalid',
  'identity_taken',
  'identity_mismatch',
] as const

/**
 * The text for an OAuth outcome. A failed re-authentication (`reauth_stale`, ADR 0003 section 5
 * and #522) says what failed and offers the password when the account has one (`hasPassword`):
 * `undefined` while that is still loading (the first sentence only, so the text never flips from
 * one advice to another), `null` when it could not be read (both ways).
 */
export function oauthOutcomeMessage(
  code: string,
  hasPassword?: boolean | null,
  provider?: string | null,
): string {
  if (code === 'no_account') return noAccountMessage(provider ?? null)
  if (code === 'reauth_stale') {
    if (hasPassword === undefined) return t('oauth.reauth_stale')
    if (hasPassword === true) return t('oauth.reauth_stale_password')
    if (hasPassword === false) return t('oauth.reauth_stale_set_password')
    return t('oauth.reauth_stale_both')
  }
  const known = (OAUTH_OUTCOME_CODES as readonly string[]).includes(code)
    ? (`oauth.${code}` as CatalogueKey)
    : undefined
  return t(known ?? 'oauth.invalid')
}

const KNOWN_PROVIDERS = ['google', 'github', 'microsoft']

/**
 * A new provider account on the sign-in screen (`no_account`, #617): it has no classbeat account,
 * and creating one needs the sign-up screen and an invite. The provider comes from the callback's
 * `&provider=`; only the three known names are shown, anything else reads as "provider account".
 */
function noAccountMessage(provider: string | null): string {
  const account =
    provider !== null && KNOWN_PROVIDERS.includes(provider)
      ? t('oauth.providerAccount', { provider: providerName(provider) })
      : t('oauth.providerAccountOther')
  return t('oauth.no_account', { account })
}

function waitText(seconds: number): string {
  const minutes = Math.ceil(seconds / 60)
  return minutes > 1
    ? t('error.waitMinutes', { count: minutes })
    : t('error.waitSeconds', { count: Math.ceil(seconds) })
}

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 429) {
      return error.retryAfter === null
        ? t('error.tooMany')
        : t('error.tooManyWait', { wait: waitText(error.retryAfter) })
    }
    const key = error.reason === null ? undefined : reasonKey(error.reason)
    if (key !== undefined) return t(key)
    if (error.status === 422) return t('error.fields')
    if (error.status >= 500) return t('error.server')
  }
  if (error instanceof TypeError) return t('error.network')
  return t('error.generic')
}

export function providerName(provider: string): string {
  switch (provider) {
    case 'google':
      return 'Google'
    case 'github':
      return 'GitHub'
    case 'microsoft':
      return 'Microsoft'
    default:
      return provider
  }
}
