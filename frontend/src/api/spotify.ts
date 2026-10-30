import { ApiError, del, get, post, request } from './client'
import { flag, object, optionalCount, optionalText, text } from './decode'

// The Spotify connection (ADR 0009 section 2, backend/src/classbeat/spotify/routes.py). The
// answers hold no token, no Spotify user id and no display name. The Client ID is the user's own
// public value; the pages never print it. With the feature off every route answers 404
// `feature_off` and the pages hide.

export interface SpotifyStatus {
  /** `not_connected`, `connected` or `needs_reconnect`. */
  status: string
  /** An ADR 0009 section 8 category, never a provider message. */
  lastError: string | null
  /** The backend's plain sentence for `lastError`. */
  lastErrorMessage: string | null
  /** classbeat's fixed callback URL, built from settings, to register in the Spotify app. */
  callbackUrl: string
  poolConsent: boolean
  consentVersion: number | null
  /** The exact wording the user consents to (ADR 0009 section 9). */
  consentText: string
}

function decodeStatus(value: unknown): SpotifyStatus {
  const item = object(value, 'spotify')
  return {
    status: text(item, 'status'),
    lastError: optionalText(item, 'last_error'),
    lastErrorMessage: optionalText(item, 'last_error_message'),
    callbackUrl: text(item, 'callback_url'),
    poolConsent: flag(item, 'pool_consent'),
    consentVersion: optionalCount(item, 'consent_version'),
    consentText: text(item, 'consent_text'),
  }
}

export function isFeatureOff(error: unknown): boolean {
  return error instanceof ApiError && error.status === 404 && error.reason === 'feature_off'
}

/** The status, or `null` when song picking is switched off (the card is then hidden). */
export async function getSpotifyStatus(signal?: AbortSignal): Promise<SpotifyStatus | null> {
  try {
    const status = decodeStatus(await get('/api/connections/spotify', signal))
    return status
  } catch (error) {
    if (isFeatureOff(error)) return null
    throw error
  }
}

/** Starts a connect. The body has exactly these fields: there is no Client Secret. */
export async function startSpotify(
  clientId: string,
  confirmAccountChange: boolean,
): Promise<string> {
  const body = object(
    await post('/api/connections/spotify/start', {
      client_id: clientId,
      confirm_account_change: confirmAccountChange,
    }),
    'start',
  )
  return text(body, 'authorize_url')
}

export async function disconnectSpotify(): Promise<void> {
  await del('/api/connections/spotify')
}

export async function giveSpotifyConsent(): Promise<void> {
  await request('PUT', '/api/connections/spotify/pool-consent')
}

export async function revokeSpotifyConsent(): Promise<void> {
  await del('/api/connections/spotify/pool-consent')
}
