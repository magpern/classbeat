// The preview transport contract (ADR 0042 section 2). Pure: no React, no fetch, no audio here.
//
// A transport is something that can be told to play a position and that reports where it is. The
// editor draws its cursor ONLY from `observe()` (D1, D9): never from the command it sent, never
// from UI state. `SilentTransport` is the one transport in this PR; the rendered-clip and the
// Spotify Connect transports are later PRs and implement the same interface.

export type TransportKind = 'silent' | 'rendered' | 'spotify'

export interface TransportCapabilities {
  /** True only for a studio draft whose chosen output includes the mix. */
  exactMix: boolean
  /** The transport can play two songs overlapping and fade between them. */
  crossfades: boolean
  /** The smallest step the transport can seek to, in ms. */
  seekGranularityMs: number
  kind: TransportKind
}

export type TransportStatus =
  | 'idle'
  | 'starting'
  | 'playing'
  | 'paused'
  /** Something other than the requested recording is playing: no cursor. */
  | 'external'
  /** The recording has no usable alignment: no position. */
  | 'unaligned'
  /** The position is in the recording but outside the class. */
  | 'outside_window'
  | 'no_device'
  | 'error'

export interface TransportState {
  status: TransportStatus
  /** The piece (play-order index) the transport is on, when it is on one. */
  piece?: number
  /** The position the transport plays: the rendered clip, the Spotify recording, or class time for silence. */
  transportMs?: number
  /** When the state was read, in the transport clock's ms. */
  observedAt: number
  /** How far `transportMs` may be off, in ms. */
  uncertaintyMs: number
  /** A short code for `external`, `unaligned`, `outside_window`, `no_device` and `error`. */
  reason?: string
}

/** What `audition` is asked to play: a piece at a position of its recording, and the class instant that is. */
export interface AuditionRequest {
  piece: number
  sourceMs: number
  classMs: number
}

export type Ack = { ok: true } | { ok: false; reason: string }

export interface PreviewTransport {
  readonly capabilities: TransportCapabilities
  /** Starts playing at the request. Called from a user gesture each time. */
  audition(request: AuditionRequest): Promise<Ack>
  pause(): Promise<void>
  /** Where the transport is now. Never inferred from the commands that were sent. */
  observe(): TransportState
}

/** The states in which a position is known, so a cursor may be drawn. */
export function hasCursor(state: TransportState): boolean {
  return (state.status === 'playing' || state.status === 'paused') && state.transportMs !== undefined
}
