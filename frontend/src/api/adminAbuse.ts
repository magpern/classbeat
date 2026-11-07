import { get } from './client'
import { count, flag, list, object, optionalText, text, type JsonObject } from './decode'

// The admin Abuse view (backend/src/classbeat/admin/abuse.py, #632 item 3): who sends many
// requests, who hit the rate limit, and which sessions were seen from several networks, over a
// short window of the privacy-safe security log. The server sends no address, hash, session id or
// route; server text is data, rendered as text only.

export const ABUSE_WINDOWS = [15, 60, 240] as const

export interface Subject {
  userId: string
  email: string | null
  role: string | null
  disabled: boolean
}

export interface TopUser extends Subject {
  requests: number
  perMinute: number
  busiestMinute: number
  rateLimited: number
  networks: number
  sessions: number
}

export interface LimitHit extends Subject {
  hits: number
  firstAt: string
  lastAt: string
}

export interface MovedSession extends Subject {
  networks: number
  requests: number
  firstAt: string
  lastAt: string
}

export interface Abuse {
  minutes: number
  requests: number
  rateLimited: number
  users: number
  topUsers: TopUser[]
  limitHits: LimitHit[]
  movedSessions: MovedSession[]
  topUsersTruncated: boolean
  limitHitsTruncated: boolean
  movedSessionsTruncated: boolean
}

function subject(item: JsonObject): Subject {
  return {
    userId: text(item, 'user_id'),
    email: optionalText(item, 'email'),
    role: optionalText(item, 'role'),
    disabled: flag(item, 'disabled'),
  }
}

export function abusePath(minutes: number): string {
  return `/api/admin/abuse?minutes=${String(minutes)}`
}

export async function getAbuse(minutes: number, signal?: AbortSignal): Promise<Abuse> {
  const body = object(await get(abusePath(minutes), signal), 'abuse')
  return {
    minutes: count(body, 'minutes'),
    requests: count(body, 'requests'),
    rateLimited: count(body, 'rate_limited'),
    users: count(body, 'users'),
    topUsers: list(body, 'top_users').map((row) => {
      const item = object(row, 'top user')
      return {
        ...subject(item),
        requests: count(item, 'requests'),
        perMinute: count(item, 'per_minute'),
        busiestMinute: count(item, 'busiest_minute'),
        rateLimited: count(item, 'rate_limited'),
        networks: count(item, 'networks'),
        sessions: count(item, 'sessions'),
      }
    }),
    limitHits: list(body, 'limit_hits').map((row) => {
      const item = object(row, 'limit hit')
      return {
        ...subject(item),
        hits: count(item, 'hits'),
        firstAt: text(item, 'first_at'),
        lastAt: text(item, 'last_at'),
      }
    }),
    movedSessions: list(body, 'moved_sessions').map((row) => {
      const item = object(row, 'moved session')
      return {
        ...subject(item),
        networks: count(item, 'networks'),
        requests: count(item, 'requests'),
        firstAt: text(item, 'first_at'),
        lastAt: text(item, 'last_at'),
      }
    }),
    topUsersTruncated: flag(body, 'top_users_truncated'),
    limitHitsTruncated: flag(body, 'limit_hits_truncated'),
    movedSessionsTruncated: flag(body, 'moved_sessions_truncated'),
  }
}
