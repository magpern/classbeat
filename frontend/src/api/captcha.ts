import { DecodeError, object, text } from './decode'

// Turnstile's session config and the token field of the protected routes (ADR 0025).

/**
 * `turnstile`: Cloudflare's widget with the public site key. `fake`: the e2e stack's local
 * stand-in (`CLASSBEAT_TURNSTILE_FAKE`, refused in prod by the server), which loads nothing.
 */
export type CaptchaConfig = { kind: 'turnstile'; siteKey: string } | { kind: 'fake' }

/** The routes' `action` (ADR 0025 section 1): a token solved for one form passes no other. */
export type CaptchaAction = 'signin' | 'signup' | 'signup_email' | 'reset' | 'invite_request'

export function decodeCaptcha(value: unknown): CaptchaConfig | null {
  if (value === undefined || value === null) return null
  const captcha = object(value, 'captcha')
  if (captcha.fake === true) return { kind: 'fake' }
  const siteKey = text(captcha, 'site_key')
  if (siteKey === '') throw new DecodeError('site_key')
  return { kind: 'turnstile', siteKey }
}

/** The token as the protected routes read it; no field at all while Turnstile is off. */
export function captchaField(token: string | null | undefined): {
  captcha_token?: string
} {
  return token ? { captcha_token: token } : {}
}
