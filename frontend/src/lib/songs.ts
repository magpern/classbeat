import { ApiError } from '../api/client'
import { getConnections } from '../api/connections'
import type { Brief } from '../api/jobs'
import { isClassBusy } from '../api/songMessages'

// Helpers of the song pages: times as the page shows them, the brief a follow-up job needs, the
// user's current connection ids, and the wait after \`class_busy\`.

export function minutes(totalSec: number): string {
  const whole = Math.max(0, Math.round(totalSec))
  const m = Math.floor(whole / 60)
  const s = whole % 60
  return `${String(m)}:${String(s).padStart(2, '0')}`
}

export function roleLabel(role: string): string {
  const spaced = role.replace(/_/g, ' ')
  return spaced.charAt(0).toUpperCase() + spaced.slice(1)
}

/** A date and time for the expiry; a value that is not a time is shown as given. */
export function whenText(iso: string): string {
  const at = new Date(iso)
  return Number.isNaN(at.getTime()) ? iso : at.toLocaleString()
}

export const MAX_BRIEF_TEXT = 2000

/**
 * The brief of a follow-up job (Ask for changes, pick again, create the playlist again). The
 * original form is kept in this tab; without it the brief is rebuilt from the song list's asked
 * time, so the follow-up still asks for the same length.
 */
export function followUpBrief(kept: Brief | null, targetSec: number, note: string): Brief {
  const extra = note.trim()
  if (kept !== null) {
    const joined = extra === '' ? kept.text : kept.text === '' ? extra : `${kept.text}\n${extra}`
    return { ...kept, text: joined.slice(0, MAX_BRIEF_TEXT) }
  }
  const length = Math.min(180, Math.max(10, Math.round(targetSec / 60)))
  return {
    length_min: length,
    intensity: 'moderate',
    climbs: 0,
    sprints: 0,
    races: 0,
    ftp_test: null,
    style: '',
    text: extra.slice(0, MAX_BRIEF_TEXT),
  }
}

export class NotConnected extends Error {
  constructor() {
    super('not_connected')
    this.name = 'NotConnected'
  }
}

/** The ids a job input names: the user's current, connected MOWL and Spotify connections. */
export async function currentConnectionIds(): Promise<{ mowl: string; spotify: string }> {
  const all = await getConnections()
  const mowl = all.find((c) => c.provider === 'mowl' && c.status === 'connected')
  const spotify = all.find((c) => c.provider === 'spotify' && c.status === 'connected')
  if (mowl === undefined || spotify === undefined) throw new NotConnected()
  return { mowl: mowl.id, spotify: spotify.id }
}

/** Text for an error of a follow-up: a missing connection reads like the server's refusal. */
export function isNotConnected(error: unknown): boolean {
  return (
    error instanceof NotConnected ||
    (error instanceof ApiError && error.status === 409 && error.reason === 'not_connected')
  )
}

export const timing = { classBusyPauseMs: 1000, classBusyTries: 4 }

/**
 * Runs \`start\` (a job creation). A \`class_busy\` answer means the cancel before it is still
 * settling (ADR 0008 section 5): wait a moment and try again with the same request and key.
 */
export async function startAfterCancel<T>(start: () => Promise<T>): Promise<T> {
  for (let attempt = 1; ; attempt++) {
    try {
      return await start()
    } catch (error) {
      if (!isClassBusy(error) || attempt >= timing.classBusyTries) throw error
      await new Promise((resolve) => setTimeout(resolve, timing.classBusyPauseMs))
    }
  }
}

/** A short stable text for an idempotency scope (FNV-1a), so a changed request gets a new key. */
export function digest(value: string): string {
  let hash = 0x811c9dc5
  for (let i = 0; i < value.length; i++) {
    hash ^= value.charCodeAt(i)
    hash = Math.imul(hash, 0x01000193) >>> 0
  }
  return hash.toString(36)
}
