import { del, get, post } from './client'
import { flag, object, optionalText, text } from './decode'

// The classbeat-player connection (ADR 0034 section 3, backend/src/classbeat/player_link/routes.py).
// The answers hold no token and no link id. Connecting leaves for the player's own page and comes
// back to `/account/player?result=...`; nothing starts until the user presses Connect.

/** The address the callback returns to; it opens the player's dialog on Connections. */
export const PLAYER_PATH = '/account/player'

export interface PlayerStatus {
  /** `not_connected`, `connected` or `needs_reconnect`. */
  status: string
  /** A category, never a provider message. */
  lastError: string | null
  connectedAt: string | null
  /** False while the sign-in provider or the player client is off: Connect is then not offered. */
  linkEnabled: boolean
}

export function decodePlayerStatus(value: unknown): PlayerStatus {
  const item = object(value, 'player status')
  return {
    status: text(item, 'status'),
    lastError: optionalText(item, 'last_error'),
    connectedAt: optionalText(item, 'connected_at'),
    linkEnabled: flag(item, 'link_enabled'),
  }
}

export async function getPlayerStatus(signal?: AbortSignal): Promise<PlayerStatus> {
  return decodePlayerStatus(await get('/api/connections/player', signal))
}

/** The player's own address to send the browser to (it needs a recent sign-in: the guard asks). */
export async function startPlayerLink(): Promise<string> {
  return text(object(await post('/api/connections/player/start'), 'start'), 'link_url')
}

export interface PlayerDisconnected {
  disconnected: boolean
  /** The link may still be listed on the player's site, and can be removed there. */
  linkMayRemain: boolean
}

export async function disconnectPlayer(): Promise<PlayerDisconnected> {
  const body = object(await del('/api/connections/player'), 'disconnect')
  return { disconnected: flag(body, 'disconnected'), linkMayRemain: flag(body, 'link_may_remain') }
}
