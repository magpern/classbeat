import type { Clip } from '../api/previewClips'
import type { Win } from './clipSlots'
import type { TransportState } from './transport'

// Pure rules of the rendered-clip transport (ADR 0042 section 6.4). No React, no DOM, no clock.
// Where a window starts and ends is the server's (`preview_window_calc`): nothing here places one.

/** The next window is requested when this much of the playing one is left. */
export const PRELOAD_LEAD_MS = 90_000
/** A window that ends this close to the mix end is the last one. */
const END_SLACK_MS = 100
/** The cursor's uncertainty: one element switch at a join. */
const UNCERTAINTY_MS = 100

/** The first of `windows` that is current (not stale) and holds `ms`. */
export function windowAt(windows: readonly (Win | null)[], ms: number): Win | null {
  for (const win of windows) {
    if (win !== null && !win.stale && ms >= win.clip.startMs && ms < win.clip.endMs) return win
  }
  return null
}

export function isLast(clip: Clip, mixEndMs: number): boolean {
  return clip.endMs >= mixEndMs - END_SLACK_MS
}

/** Whether the next window should be asked for now: it exists, nothing is on its way, and the playing one is nearly over. */
export function wantsPreload(clip: Clip, positionMs: number, mixEndMs: number): boolean {
  return !isLast(clip, mixEndMs) && clip.endMs - positionMs <= PRELOAD_LEAD_MS
}

/** What the transport knows, without the elements. */
export interface Facts {
  observedAt: number
  piece: number | undefined
  positionMs: number
  /** A hard stop with a reason (the session may still be alive). */
  problem: string | null
  hasSession: boolean
  /** What the transport rests in with no session. */
  rest: { status: 'idle' | 'paused' | 'error'; reason?: string }
  listening: boolean
  hasWindow: boolean
  /** A clip is being asked for (a seek or a start). */
  loading: { rendering: boolean } | null
  waitingForNext: boolean
  elementPaused: boolean
}

/** `observe()`'s answer from the facts. */
export function viewOf(f: Facts): TransportState {
  const base = { observedAt: f.observedAt, uncertaintyMs: UNCERTAINTY_MS, ...(f.piece === undefined ? {} : { piece: f.piece }) }
  const at = { transportMs: f.positionMs }
  if (f.problem !== null) return { status: 'error', ...at, reason: f.problem, ...base }
  if (!f.hasSession) {
    if (f.rest.status === 'idle') return { status: 'idle', observedAt: f.observedAt, uncertaintyMs: 0 }
    return { status: f.rest.status, ...at, ...(f.rest.reason === undefined ? {} : { reason: f.rest.reason }), ...base }
  }
  if (f.listening) return { status: 'playing', ...at, ...base }
  if (f.hasWindow && f.loading === null && !f.waitingForNext && f.elementPaused) return { status: 'paused', ...at, ...base }
  const reason = f.loading?.rendering === true ? 'rendering' : f.waitingForNext ? 'next_part' : undefined
  return { status: 'starting', ...at, ...(reason === undefined ? {} : { reason }), ...base }
}
