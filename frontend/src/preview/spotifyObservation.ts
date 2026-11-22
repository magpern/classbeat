import type { PlayerState } from '../api/spotifyPreview'

// What the Spotify transport has seen of the player (ADR 0042 sections 2 and 7.3). Pure: no React,
// no fetch, no clock (the clock is an argument).
//
// An observation is one answer of the state proxy with when it was asked and answered. Answers can
// arrive out of order, so an older one never replaces a newer one: each ask has a sequence number
// and an epoch (a command bumps the epoch, so an answer asked before it is stale), and Spotify's own
// `timestamp` may not go back. The position of the player is `progressMs` at `observedAt`, plus the
// time since while it plays: that is the only source of the cursor.

/** The drift beyond which the position is taken as a seek (ADR 0042 section 7.3), in ms. */
export const SEEK_MS = 1500

export interface Observation {
  /** The ask's sequence number: increasing in the order the asks were made. */
  seq: number
  /** The epoch the ask was made in. */
  epoch: number
  /** When the player was read: the middle of the round trip, in the transport clock's ms. */
  observedAt: number
  /** Half the round trip of the ask (browser to Spotify and back), in ms. */
  halfRoundTripMs: number
  state: PlayerState
}

export type Note = 'seek_detected' | 'device_switched'

export type Verdict =
  | { accepted: true; notes: Note[] }
  | { accepted: false; why: 'older_epoch' | 'older_seq' | 'older_timestamp' }

/** Builds the observation of an answer asked at `sentAt` and answered at `receivedAt`. */
export function observationOf(seq: number, epoch: number, sentAt: number, receivedAt: number, state: PlayerState): Observation {
  const trip = Math.max(0, receivedAt - sentAt)
  return { seq, epoch, observedAt: sentAt + trip / 2, halfRoundTripMs: trip / 2, state }
}

/** Where the player is at `now` according to `seen`: its progress, moving on while it plays; null: no position. */
export function positionAt(seen: Observation, now: number): number | null {
  const progress = seen.state.progressMs
  if (progress === null) return null
  return seen.state.status === 'playing' ? progress + Math.max(0, now - seen.observedAt) : progress
}

/**
 * How far the position may be off at `now`: half the round trip, plus the age of the poll (the time
 * since its answer arrived) while the player moves. A paused player does not move, so its age adds nothing.
 */
export function uncertaintyAt(seen: Observation, now: number): number {
  const age = seen.state.status === 'playing' ? Math.max(0, now - (seen.observedAt + seen.halfRoundTripMs)) : 0
  return Math.round(seen.halfRoundTripMs + age)
}

function samePiece(a: PlayerState, b: PlayerState): boolean {
  return a.pieces.length > 0 && a.pieces.some((p) => b.pieces.includes(p))
}

function deviceKey(state: PlayerState): string | null {
  return state.device === null ? null : `${state.device.type}:${state.device.name}`
}

/**
 * Whether `next` may replace `held` (the newest accepted observation), and what changed. An answer
 * from an older epoch, an earlier ask or an earlier Spotify timestamp is refused: it is older than
 * what is held. A position that differs from the expected one by more than `SEEK_MS` is a seek, and
 * a device that changed while the same song plays is a device switch; both only note, the newest
 * observation is the one used.
 */
export function judge(held: Observation | null, next: Observation, epoch: number): Verdict {
  if (next.epoch !== epoch) return { accepted: false, why: 'older_epoch' }
  if (held === null || held.epoch !== next.epoch) return { accepted: true, notes: [] }
  if (next.seq <= held.seq) return { accepted: false, why: 'older_seq' }
  const was = held.state.timestamp
  const is = next.state.timestamp
  if (was !== null && is !== null && is < was) return { accepted: false, why: 'older_timestamp' }
  const notes: Note[] = []
  const expected = positionAt(held, next.observedAt)
  const got = next.state.progressMs
  if (expected !== null && got !== null && samePiece(held.state, next.state) && Math.abs(got - expected) > SEEK_MS) {
    notes.push('seek_detected')
  }
  const before = deviceKey(held.state)
  const after = deviceKey(next.state)
  if (before !== null && after !== null && before !== after) notes.push('device_switched')
  return { accepted: true, notes }
}

/** Polling runs only while the transport is starting or playing, on a visible page, and not before a rate limit ends. */
export function pollDelayMs(wanted: boolean, visible: boolean, now: number, notBefore: number | null, everyMs = 1000): number | null {
  if (!wanted || !visible) return null
  return notBefore !== null && notBefore > now ? Math.ceil(notBefore - now) : everyMs
}
