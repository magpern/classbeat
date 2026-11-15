import { post } from './client'
import { object, text } from './decode'

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

