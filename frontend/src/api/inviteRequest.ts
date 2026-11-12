import { resolveBrowserLocale } from '../i18n/locales'
import { captchaField } from './captcha'
import { get, post } from './client'
import { isObject } from './decode'

// The public side of invite requests (ADR 0033, backend/src/classbeat/auth/invite_request_routes.py).
// The answer to a request is always 202 `received`, whatever the address, so nothing here can tell
// a caller whether an address is known.

/** The effective sign-up mode from `GET /api/settings/public`; null when it could not be read. */
export async function getSignupMode(signal?: AbortSignal): Promise<'invite' | 'open' | 'closed' | null> {
  const body = await get('/api/settings/public', signal)
  const mode = isObject(body) ? body.signup_mode : undefined
  return mode === 'invite' || mode === 'open' || mode === 'closed' ? mode : null
}

export interface InviteRequestInput {
  email: string
  heardFrom: string
  instructor: boolean
  captchaToken?: string | null
}

export async function requestInvite(input: InviteRequestInput): Promise<void> {
  await post('/api/auth/invite-request', {
    email: input.email,
    heard_from: input.heardFrom.trim() === '' ? null : input.heardFrom.trim(),
    instructor: input.instructor,
    locale: resolveBrowserLocale(),
    ...captchaField(input.captchaToken),
  })
}
