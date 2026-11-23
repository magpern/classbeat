import type { AuditionAck, DeviceView, PlayerState, PreviewFailure, Result } from '../api/spotifyPreview'
import { describeFailure } from './spotifyErrorModel'
import { judge, observationOf, positionAt, uncertaintyAt, type Note, type Observation } from './spotifyObservation'
import { transportPositionMs } from './spotifyAlignmentModel'
import { spotifyCursor, type SpotifyModel } from './timeModel'
import type { Ack, AuditionRequest, PreviewTransport, TransportCapabilities, TransportState } from './transport'

// The Spotify Connect transport (ADR 0042 sections 2, 7.2 and 7.3): the user's own Spotify app
// plays ONE original track at a time (no crossfade here), and this transport reports where it is.
//
// THE CURSOR IS DRAWN ONLY FROM WHAT THE PLAYER WAS OBSERVED TO DO. `audition` sends a play and
// answers whether it was accepted; it never sets a position. `observe()` is computed from the newest
// accepted observation of the state proxy (and the time since), through the class's Spotify mapping
// `sourceMs = transportMs - alignment - correction`, `classMs = S_i + sourceMs`. Nothing plays by
// itself: every play is a user gesture, a page load starts nothing, and nothing resumes.

export interface SpotifyPlayerApi {
  audition(piece: number, positionMs: number): Promise<Result<AuditionAck>>
  pause(): Promise<Result<PlayerState>>
  state(): Promise<Result<PlayerState>>
}

export interface SpotifyTransportOptions {
  api: SpotifyPlayerApi
  model: SpotifyModel
  /** The clock in ms; injected so a test owns time. */
  now: () => number
  /** How long after a play was accepted another track or nothing may still be the old state, in ms. */
  startingGraceMs?: number
}

const CAPABILITIES: TransportCapabilities = {
  exactMix: false,
  crossfades: false, // one original track at a time: the rider's own Spotify crossfade is not heard here
  seekGranularityMs: 1000,
  kind: 'spotify',
}

export const STARTING_GRACE_MS = 6000
/** How long a note (a seek, a device switch) stays on the view, in ms. */
export const NOTE_MS = 5000
/** A 429 without a Retry-After waits this long, doubling for each one in a row up to the cap, in seconds. */
export const DEFAULT_BACKOFF_S = 5
export const MAX_BACKOFF_S = 60

/** The things the page shows beside the contract's state. */
export interface SpotifyView {
  state: TransportState
  /** The class instant of the cursor; null: no cursor. */
  classMs: number | null
  /** The cursor is inside an overlap region: its band is labelled. */
  inOverlap: boolean
  /** `uncertaintyMs` either side of `classMs`, when there is a cursor. */
  band: { fromMs: number; toMs: number } | null
  note: Note | null
  device: DeviceView | null
  failure: PreviewFailure | null
}

/** A call that throws (a bug, not an answer) is a failure the page shows, never a silent success. */
async function guard<T>(call: () => Promise<Result<T>>): Promise<Result<T>> {
  try {
    return await call()
  } catch {
    return { ok: false, failure: { reason: 'unknown', status: 0, retryAfterS: null } }
  }
}

interface Intent {
  piece: number
  acceptedAt: number
  /** The requested song was seen playing: from then on another track is not a stale state. */
  confirmed: boolean
}

export class SpotifyConnectTransport implements PreviewTransport {
  readonly capabilities = CAPABILITIES
  private readonly api: SpotifyPlayerApi
  private model: SpotifyModel
  private readonly now: () => number
  private readonly grace: number
  private epoch = 0
  private seq = 0
  private held: Observation | null = null
  private intent: Intent | null = null
  private failed: PreviewFailure | null = null
  private noted: { note: Note; at: number } | null = null
  private pending = 0
  private rateLimitedUntil: number | null = null
  private rateLimitStreak = 0

  constructor(options: SpotifyTransportOptions) {
    this.api = options.api
    this.model = options.model
    this.now = options.now
    this.grace = options.startingGraceMs ?? STARTING_GRACE_MS
  }

  /** A new mapping (a correction was set or cleared): the cursor is derived through it from the next read on. */
  setModel(model: SpotifyModel): void {
    this.model = model
  }

  async audition(request: AuditionRequest): Promise<Ack> {
    const song = this.model.songs.find((s) => s.piece === request.piece)
    if (song === undefined || !Number.isFinite(request.sourceMs)) return { ok: false, reason: 'unknown_piece' }
    const positionMs = transportPositionMs(song, request.sourceMs)
    this.newEpoch() // an answer asked before this play is stale
    this.intent = null
    this.failed = null
    this.rateLimitedUntil = null
    this.rateLimitStreak = 0
    const answer = await guard(() => this.api.audition(request.piece, positionMs))
    if (!answer.ok) {
      this.fail(answer.failure)
      return { ok: false, reason: answer.failure.reason }
    }
    // The play was accepted. The position the page draws still comes from the first observation.
    this.intent = { piece: request.piece, acceptedAt: this.now(), confirmed: false }
    return { ok: true }
  }

  async pause(): Promise<void> {
    const sentAt = this.now()
    this.newEpoch()
    const epoch = this.epoch
    const seq = ++this.seq
    const answer = await guard(() => this.api.pause())
    if (!answer.ok) return this.fail(answer.failure)
    this.take(observationOf(seq, epoch, sentAt, this.now(), answer.value))
  }

  /** Reads the player once. A reordered or stale answer is dropped; a failure is kept for `view()`. */
  async poll(): Promise<void> {
    if (this.pending >= 2) return // an answer is already slow: do not pile asks up
    const sentAt = this.now()
    const epoch = this.epoch
    const seq = ++this.seq
    this.pending += 1
    try {
      const answer = await guard(() => this.api.state())
      if (epoch !== this.epoch) return
      if (!answer.ok) return this.fail(answer.failure)
      this.failed = null
      this.rateLimitedUntil = null
      this.rateLimitStreak = 0
      this.take(observationOf(seq, epoch, sentAt, this.now(), answer.value))
    } finally {
      this.pending -= 1
    }
  }

  /**
   * True while polling is useful: a play is starting, or the player was observed playing (also while
   * the cursor is `unaligned` or `outside_window`), and nothing needs the person first. A transient
   * failure keeps polling (after its `Retry-After`); one that needs the person stops it.
   */
  wantsPolling(): boolean {
    if (this.intent === null) return false
    if (this.failed !== null) return !describeFailure(this.failed).stops
    const status = this.observe().status
    if (status === 'starting') return true
    return this.held?.state.status === 'playing' && (status === 'playing' || status === 'unaligned' || status === 'outside_window')
  }

  /** The earliest time the next poll may be asked (a 429's `Retry-After`); null: no limit. */
  notBefore(): number | null {
    return this.rateLimitedUntil
  }

  observe(): TransportState {
    return this.view().state
  }

  view(): SpotifyView {
    const now = this.now()
    const none = (status: TransportState['status'], reason?: string, extra: Partial<SpotifyView> = {}): SpotifyView => ({
      state: { status, observedAt: now, uncertaintyMs: 0, ...(reason === undefined ? {} : { reason }) },
      classMs: null,
      inOverlap: false,
      band: null,
      note: null,
      device: null,
      failure: this.failed,
      ...extra,
    })
    if (this.failed !== null) {
      const reason = this.failed.reason
      return none(reason === 'no_device' ? 'no_device' : 'error', reason)
    }
    const intent = this.intent
    if (intent === null) return none('idle')
    const seen = this.held
    const starting = !intent.confirmed && now - intent.acceptedAt < this.grace
    if (seen === null) return none(starting ? 'starting' : 'idle', starting ? undefined : 'no_answer')
    const device = seen.state.device
    const position = positionAt(seen, now)
    const requested = seen.state.pieces.includes(intent.piece) && position !== null
    // A paused read of the requested song inside the grace is the state from before the play (a replay
    // of a song just paused): Spotify has not started it yet, so it is not final. After the grace it is.
    if (starting && seen.state.status === 'paused') return none('starting', undefined, { device })
    if (seen.state.status === 'idle' || seen.state.status === 'external' || !requested) {
      // Still the state from before the play, or another track: wait for the grace, then say so.
      if (starting) return none('starting', undefined, { device })
      return seen.state.status === 'idle' ? none('idle', 'ended') : none('external', 'other_track', { device })
    }
    const uncertaintyMs = uncertaintyAt(seen, now)
    const note = this.noted !== null && now - this.noted.at < NOTE_MS ? this.noted.note : null
    const cursor = spotifyCursor(this.model, intent.piece, { requestedTrack: true, transportMs: position })
    const base = { observedAt: now, uncertaintyMs, piece: intent.piece }
    if (cursor.status === 'unaligned') return none('unaligned', 'timing_unusable', { device })
    if (cursor.status === 'outside_window') {
      return none('outside_window', cursor.sourceMs < 0 ? 'before' : 'after', { device })
    }
    if (cursor.status === 'external') return none('external', 'other_track', { device })
    const status = seen.state.status === 'paused' ? 'paused' : 'playing'
    return {
      state: { ...base, status, transportMs: position, ...(note === null ? {} : { reason: note }) },
      classMs: cursor.classMs,
      inOverlap: cursor.inOverlap,
      band: { fromMs: cursor.classMs - uncertaintyMs, toMs: cursor.classMs + uncertaintyMs },
      note,
      device,
      failure: null,
    }
  }

  private newEpoch(): void {
    this.epoch += 1
    this.held = null
    this.noted = null
  }

  private take(next: Observation): void {
    const verdict = judge(this.held, next, this.epoch)
    if (!verdict.accepted) return
    this.held = next
    if (this.intent !== null && next.state.status === 'playing' && next.state.pieces.includes(this.intent.piece)) {
      this.intent.confirmed = true
    }
    const note = verdict.notes.includes('seek_detected') ? 'seek_detected' : verdict.notes[0]
    if (note !== undefined) this.noted = { note, at: this.now() }
  }

  private fail(failure: PreviewFailure): void {
    this.failed = failure
    if (failure.reason !== 'rate_limited') return
    // Without a Retry-After the page must still not ask again every second: back off, longer each time.
    const backoff = Math.min(DEFAULT_BACKOFF_S * 2 ** this.rateLimitStreak, MAX_BACKOFF_S)
    this.rateLimitStreak += 1
    this.rateLimitedUntil = this.now() + (failure.retryAfterS ?? backoff) * 1000
  }
}
