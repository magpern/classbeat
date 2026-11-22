import type { Ack, AuditionRequest, PreviewTransport, TransportCapabilities, TransportState } from './transport'

// A clock-driven cursor with no audio (ADR 0042 section 2): the transport the editor has before any
// audio exists. Its "position" is class time, so `transportMs` is a class instant. It reads
// the clock at `observe()` rather than keeping a timer, so the cursor can only be what the clock says.

export interface SilentTransportOptions {
  /** The class length in ms: the cursor stops there. */
  totalMs: number
  /** The clock in ms; injected so a test owns time. */
  now: () => number
}

const CAPABILITIES: TransportCapabilities = {
  exactMix: false,
  crossfades: false,
  seekGranularityMs: 1,
  kind: 'silent',
}

export class SilentTransport implements PreviewTransport {
  readonly capabilities = CAPABILITIES
  private readonly totalMs: number
  private readonly now: () => number
  private mode: 'idle' | 'playing' | 'paused' = 'idle'
  private basePieceMs = 0
  private startedAt = 0
  private piece: number | undefined

  constructor(options: SilentTransportOptions) {
    this.totalMs = options.totalMs
    this.now = options.now
  }

  audition(request: AuditionRequest): Promise<Ack> {
    if (!Number.isFinite(request.classMs) || request.classMs < 0 || request.classMs >= this.totalMs) {
      return Promise.resolve({ ok: false, reason: 'outside_class' })
    }
    this.mode = 'playing'
    this.basePieceMs = request.classMs
    this.startedAt = this.now()
    this.piece = request.piece
    return Promise.resolve({ ok: true })
  }

  pause(): Promise<void> {
    if (this.mode === 'playing') {
      this.basePieceMs = this.position(this.now())
      this.mode = 'paused'
    }
    return Promise.resolve()
  }

  observe(): TransportState {
    const observedAt = this.now()
    if (this.mode === 'idle') return { status: 'idle', observedAt, uncertaintyMs: 0 }
    const position = this.mode === 'playing' ? this.position(observedAt) : this.basePieceMs
    if (position >= this.totalMs) {
      // The class is over: the clock ran out, nothing is playing.
      this.mode = 'idle'
      return { status: 'idle', observedAt, uncertaintyMs: 0 }
    }
    const base = { observedAt, uncertaintyMs: 0, transportMs: position, ...(this.piece === undefined ? {} : { piece: this.piece }) }
    return { status: this.mode, ...base }
  }

  private position(at: number): number {
    return this.basePieceMs + Math.max(0, at - this.startedAt)
  }
}
