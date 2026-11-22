import type { AuditionAck, DeviceView, PlayerState, PlayerStatus, PreviewFailure, Result } from '../api/spotifyPreview'
import type { SpotifyPlayerApi } from '../preview/spotifyConnectTransport'

// A fake Spotify player behind the owner-scoped proxy, for tests: NO network and no real account. It
// has its own virtual clock, so latency, jitter and out-of-order answers are exact. A call reaches the
// player after `out` ms (that is when the player is read) and its answer reaches the page after
// `back` more ms, so a slow answer can arrive after a newer one.

export class FakeClock {
  t = 0
  private seq = 0
  private timers: { at: number; seq: number; fn: () => void }[] = []

  readonly now = (): number => this.t

  sleep(ms: number): Promise<void> {
    if (ms <= 0) return Promise.resolve()
    return new Promise((resolve) => {
      this.timers.push({ at: this.t + ms, seq: this.seq++, fn: resolve })
    })
  }

  /** Moves time on, running every timer that falls due in order, letting the awaiting code run between them. */
  async advance(ms: number): Promise<void> {
    const target = this.t + ms
    await flush()
    for (;;) {
      this.timers.sort((a, b) => a.at - b.at || a.seq - b.seq)
      const next = this.timers[0]
      if (next === undefined || next.at > target) break
      this.timers.shift()
      this.t = next.at
      next.fn()
      await flush()
    }
    this.t = target
  }
}

/** Lets pending promise callbacks run (real macrotask, not the fake clock). */
export async function flush(): Promise<void> {
  for (let i = 0; i < 5; i += 1) await new Promise<void>((resolve) => setTimeout(resolve, 0))
}

export interface Latency {
  out: number
  back: number
}

type Call = 'audition' | 'pause' | 'state'

const EPOCH_BASE = 1_700_000_000_000

export class FakePlayer implements SpotifyPlayerApi {
  status: PlayerStatus = 'idle'
  /** The class pieces the playing track is (a track can be in the class twice); empty: not in the class. */
  pieces: number[] = []
  device: DeviceView | null = { name: 'Kitchen', type: 'Speaker' }
  /** Where the player lands when a play is accepted, instead of the requested position (a buffer, a snap); null: as asked. */
  landAt: number | null = null
  /** False: the answers carry no Spotify `timestamp`, so only the page's own ordering can refuse an old one. */
  timestamps = true
  readonly log: { call: Call; at: number; arg?: number }[] = []
  private positionMs = 0
  private since = 0
  private latencies: Partial<Record<Call, Latency[]>> = {}
  private failures: Partial<Record<Call, PreviewFailure[]>> = {}
  private throws: Partial<Record<Call, boolean>> = {}
  private calls: Record<Call, number> = { audition: 0, pause: 0, state: 0 }

  constructor(readonly clock: FakeClock) {}

  /** The latency of the next calls of `call`, in order; the last one repeats. */
  latency(call: Call, ...list: Latency[]): void {
    this.latencies[call] = list
  }

  failNext(call: Call, failure: PreviewFailure): void {
    ;(this.failures[call] ??= []).push(failure)
  }

  throwOn(call: Call, on = true): void {
    this.throws[call] = on
  }

  /** The truth of the player now. */
  position(): number {
    return this.status === 'playing' ? this.positionMs + (this.clock.t - this.since) : this.positionMs
  }

  seekTo(ms: number): void {
    this.positionMs = ms
    this.since = this.clock.t
  }

  /** Something else takes over: another track that is not in the class. */
  autoplay(): void {
    this.status = 'external'
    this.pieces = []
    this.seekTo(0)
  }

  userPauses(): void {
    this.positionMs = this.position()
    this.status = 'paused'
  }

  private latencyOf(call: Call): Latency {
    const list = this.latencies[call] ?? [{ out: 50, back: 50 }]
    const n = this.calls[call]
    return list[Math.min(n, list.length - 1)] ?? { out: 50, back: 50 }
  }

  private snapshot(): PlayerState {
    const idle = this.status === 'idle'
    return {
      status: this.status,
      pieces: this.pieces,
      progressMs: idle ? null : Math.round(this.position()),
      timestamp: this.timestamps ? EPOCH_BASE + this.clock.t : null,
      device: idle ? null : this.device,
    }
  }

  private async go<T>(call: Call, arg: number | undefined, run: () => T): Promise<Result<T>> {
    const { out, back } = this.latencyOf(call)
    this.calls[call] += 1
    this.log.push({ call, at: this.clock.t, ...(arg === undefined ? {} : { arg }) })
    if (this.throws[call] === true) throw new Error('the fake was told to throw')
    await this.clock.sleep(out)
    const failure = this.failures[call]?.shift()
    if (failure !== undefined) {
      await this.clock.sleep(back)
      return { ok: false, failure }
    }
    const value = run() // the player is read here, at the middle of the round trip
    await this.clock.sleep(back)
    return { ok: true, value }
  }

  audition(piece: number, positionMs: number): Promise<Result<AuditionAck>> {
    return this.go('audition', positionMs, () => {
      this.status = 'playing'
      this.pieces = [piece]
      this.seekTo(this.landAt ?? positionMs)
      return { pieceIndex: piece, device: this.device }
    })
  }

  pause(): Promise<Result<PlayerState>> {
    return this.go('pause', undefined, () => {
      if (this.status === 'playing' && this.pieces.length > 0) this.userPauses()
      return this.snapshot()
    })
  }

  state(): Promise<Result<PlayerState>> {
    return this.go('state', undefined, () => this.snapshot())
  }
}
