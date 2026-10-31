import { del, get, post } from './client'
import { flag, list, object, optionalText, text } from './decode'

// Provider connections (ADR 0004, backend/src/classbeat/connections/routes.py). Statuses and
// error categories come from the backend; nothing secret is ever part of these answers.

export interface Connection {
  /** The user's own connection id: what a job input names. */
  id: string
  provider: string
  /** `connected` or `needs_reconnect`. */
  status: string
  /** An ADR 0004 section 5 category, never a provider message. */
  lastError: string | null
  connectedAt: string
  lastUsedAt: string | null
  /** The provider account's email, where the page shows it (Swedebeat only). */
  account: string | null
}

/** Whether the Swedebeat card may show its form (ADR 0015): the flag is on and the server holds the app key. */
export interface SwedebeatAccess {
  enabled: boolean
  available: boolean
}

function decodeConnection(value: unknown): Connection {
  const item = object(value, 'connection')
  return {
    id: text(item, 'id'),
    provider: text(item, 'provider'),
    status: text(item, 'status'),
    lastError: optionalText(item, 'last_error'),
    connectedAt: text(item, 'connected_at'),
    lastUsedAt: optionalText(item, 'last_used_at'),
    account: optionalText(item, 'account'),
  }
}

export async function getConnections(): Promise<Connection[]> {
  const body = object(await get('/api/connections'), 'connections')
  return list(body, 'connections').map(decodeConnection)
}

/** Sends the MOWL email and password once, in the JSON body (never the URL). */
export async function connectMowl(email: string, password: string): Promise<Connection> {
  return decodeConnection(await post('/api/connections/mowl', { email, password }))
}

export async function disconnectMowl(): Promise<void> {
  await del('/api/connections/mowl')
}

/** The connections and the Swedebeat access block; an answer without the block means off. */
export async function getConnectionsView(): Promise<{
  connections: Connection[]
  swedebeat: SwedebeatAccess
}> {
  const body = object(await get('/api/connections'), 'connections')
  const access = body.swedebeat === undefined ? null : object(body.swedebeat, 'swedebeat')
  return {
    connections: list(body, 'connections').map(decodeConnection),
    swedebeat:
      access === null
        ? { enabled: false, available: false }
        : { enabled: flag(access, 'enabled'), available: flag(access, 'available') },
  }
}

/**
 * Sends the Swedebeat email and password once, in the JSON body (never the URL). `force` is true
 * only after the user confirmed that another device (the Swedebeat app) may be signed out.
 */
export async function connectSwedebeat(
  email: string,
  password: string,
  force: boolean,
): Promise<Connection> {
  return decodeConnection(await post('/api/connections/swedebeat', { email, password, force }))
}

export async function disconnectSwedebeat(): Promise<void> {
  await del('/api/connections/swedebeat')
}
