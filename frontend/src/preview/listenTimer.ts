// The listening counter of a preview session (ADR 0042 section 6.3). Pure: no React, no DOM, no
// audio, no timers; the monotonic clock is injected.
//
// It measures ELAPSED LISTENING TIME on a monotonic clock (`performance.now()`), never differences
// of a media element's `currentTime`, which jump on a seek. The caller starts it on the active
// element's `playing` event and stops it on `pause`, `waiting`, `stalled`, `seeking`, `ended` and
// `emptied`; the total is the sum of (stop - start) over those intervals. Buffering, pauses, seeks
// in progress, rendering and the gaps between windows are never inside an interval. It is not
// persisted, and nothing here adds time on a timer: a tick only asks `reached`.

/** 15 minutes of accumulated playback. */
export const LISTEN_LIMIT_MS = 15 * 60 * 1000

export class ListenTimer {
  private readonly now: () => number
  private accumulatedMs = 0
  private startedAt: number | null = null

  constructor(now: () => number) {
    this.now = now
  }

  get running(): boolean {
    return this.startedAt !== null
  }

  /** Starts an interval; a second start while one is running changes nothing. */
  start(): void {
    this.startedAt ??= this.now()
  }

  /** Ends the running interval and adds its length; a stop with none running changes nothing. */
  stop(): void {
    if (this.startedAt === null) return
    this.accumulatedMs += Math.max(0, this.now() - this.startedAt)
    this.startedAt = null
  }

  /** The total so far: the finished intervals and the one running. */
  elapsedMs(): number {
    return this.accumulatedMs + (this.startedAt === null ? 0 : Math.max(0, this.now() - this.startedAt))
  }

  /** True once the total has reached the limit. */
  reached(limitMs: number = LISTEN_LIMIT_MS): boolean {
    return this.elapsedMs() >= limitMs
  }

  /** A new session: the total is 0 and nothing runs. */
  reset(): void {
    this.accumulatedMs = 0
    this.startedAt = null
  }
}

/** The media events that stop the counter, as the element reports them. */
export const STOP_EVENTS = ['pause', 'waiting', 'stalled', 'seeking', 'ended', 'emptied'] as const
