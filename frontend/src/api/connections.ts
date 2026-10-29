import { del, get, post } from './client'
import { list, object, optionalText, text } from './decode'

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
