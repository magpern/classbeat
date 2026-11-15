import { del, get, post } from './client'
import { flag, object, optionalText, text } from './decode'

// The consent step of the OpenID Connect provider for classbeat-player (ADR 0034 section 1,
// backend/src/classbeat/auth/oidc/authorize_routes.py). The request id `r` is bound to this
// browser's flow cookie; the answer is the address to send the browser to: the player's own
// redirect address with a code, or with `access_denied`.

/** The server refuses an unknown, expired or other browser's request with this reason (400). */
export const REQUEST_EXPIRED = 'request_expired'

export async function answerPlayerConsent(requestId: string, allow: boolean): Promise<string> {
  const answer = object(await post('/oidc/authorize/consent', { r: requestId, allow }), 'consent')
  return text(answer, 'redirect')
}


// The account's own view of that consent (backend/src/classbeat/auth/oidc/consent_routes.py):
// when it was given, and a way to withdraw it. The next sign-in from the player asks again.

export interface PlayerConsent {
  /** False while the sign-in provider is off: the account page then shows nothing. */
  enabled: boolean
  /** When the user allowed the player, or null (never, or withdrawn). */
  consentAt: string | null
}

export async function getPlayerConsent(signal?: AbortSignal): Promise<PlayerConsent> {
  const body = object(await get('/api/auth/player-consent', signal), 'player consent')
  return { enabled: flag(body, 'enabled'), consentAt: optionalText(body, 'consent_at') }
}

export async function withdrawPlayerConsent(): Promise<boolean> {
  return flag(object(await del('/api/auth/player-consent'), 'withdraw'), 'withdrawn')
}
