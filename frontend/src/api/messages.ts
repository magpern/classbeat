import { ApiError } from './client'

// Plain-language texts for the `reason` codes of the auth API. Unknown codes get a generic
// message; the raw code is never shown.
const REASONS: Record<string, string> = {
  confirmation_mismatch: 'That is not the email of this account. Nothing was deleted.',
  invalid_invite: 'That invite code is not valid, or it has already been used.',
  invite_invalid: 'The invite for this sign-up is no longer valid. Ask for a new one.',
  signups_closed: 'Sign-ups are closed right now.',
  password_rule: 'Choose a password of 12 to 128 characters.',
  invalid_credentials: 'The email or password is not right.',
  wrong_password: 'That is not the password you chose when you signed up.',
  invalid_token: 'This link is not valid any more. It may have expired or been used already.',
  wrong_browser:
    'Finish on the device and browser where you started the sign-in. This link will not work here.',
  account_exists: 'An account with this address already exists. Sign in to it instead.',
  email_taken: 'That address is already used by another account.',
  last_sign_in_method: 'This is your only way to sign in, so it cannot be removed.',
  invalid_code: 'That code is not right. Check the app and try again.',
  totp_changed: 'Your two-factor setup changed meanwhile. Start again.',
  totp_not_enabled: 'Two-factor authentication is not set up.',
  not_authenticated: 'You are signed out. Sign in and try again.',
  no_pending_signup: 'No sign-in is waiting for an address. Start the provider sign-in again.',
  provider_disabled: 'That sign-in method is not available.',
  reauth_use_password: 'Your account has a password, so confirm with your password.',
  forbidden: 'Your account is not allowed to do that.',
  busy: 'The server is busy. Try again in a few seconds.',
}

const REAUTH_STALE =
  'The provider did not confirm that you signed in again in the last 5 minutes, so nothing was changed.'
const SET_A_PASSWORD =
  'To set a password, sign out and choose "Forgot your password?" on the sign-in page; then confirm with it.'

// Outcome codes the OAuth callback puts in `?oauth=` of /signin and /account.
const OAUTH_OUTCOMES: Record<string, string> = {
  invalid: 'The sign-in could not be completed. Try again.',
  denied: 'The sign-in was cancelled at the provider.',
  session_mismatch: 'You were signed out or switched account while signing in. Sign in again.',
  account_exists:
    'An account with that email already exists. Sign in to it with your password, then link the provider from your account page.',
  // Only a sign-up flow gets this (it lands on /signup, whose invite field is below the box).
  invite_required:
    'You need an invite to create an account. Enter your invite code below and try again.',
  invite_invalid: 'That invite is no longer valid. Ask for a new one.',
  identity_taken: 'That provider account is already linked to a different classbeat account.',
  identity_mismatch: 'That is not the provider account linked to your classbeat account.',
  reauth_stale: REAUTH_STALE,
  // #687: Google re-authenticates only an account without a password.
  reauth_use_password:
    'Your account has a password, so Google cannot confirm it is you. Confirm with your password. Nothing was changed.',
  reauth_expired: 'The confirmation took longer than 5 minutes, so nothing was changed. Try again.',
}

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
    if (hasPassword === undefined) return REAUTH_STALE
    if (hasPassword === true) return `${REAUTH_STALE} Use your password instead.`
    if (hasPassword === false) return `${REAUTH_STALE} ${SET_A_PASSWORD}`
    return `${REAUTH_STALE} Use your password instead, if you have one. ${SET_A_PASSWORD}`
  }
  const known = Object.hasOwn(OAUTH_OUTCOMES, code) ? OAUTH_OUTCOMES[code] : undefined
  return known ?? 'The sign-in could not be completed. Try again.'
}

const KNOWN_PROVIDERS = ['google', 'github', 'microsoft']

/**
 * A new provider account on the sign-in screen (`no_account`, #617): it has no classbeat account,
 * and creating one needs the sign-up screen and an invite. The provider comes from the callback's
 * `&provider=`; only the three known names are shown, anything else reads as "provider account".
 */
function noAccountMessage(provider: string | null): string {
  const name = provider !== null && KNOWN_PROVIDERS.includes(provider) ? `${providerName(provider)} account` : 'provider account'
  return `No classbeat account is linked to this ${name}. Create an account to join the invite-only beta.`
}

function waitText(seconds: number): string {
  const minutes = Math.ceil(seconds / 60)
  return minutes > 1 ? `${String(minutes)} minutes` : `${String(Math.ceil(seconds))} seconds`
}

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 429) {
      return error.retryAfter === null
        ? 'Too many attempts. Wait a little and try again.'
        : `Too many attempts. Try again in ${waitText(error.retryAfter)}.`
    }
    const known =
      error.reason !== null && Object.hasOwn(REASONS, error.reason) ? REASONS[error.reason] : undefined
    if (known !== undefined) return known
    if (error.status === 422) return 'Check the fields and try again.'
    if (error.status >= 500) return 'Something went wrong on the server. Try again in a moment.'
  }
  if (error instanceof TypeError) return 'The server could not be reached. Check your connection.'
  return 'That did not work. Try again.'
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
