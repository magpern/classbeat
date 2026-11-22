import { vi } from 'vitest'
import { PreviewError, type Clip, type PreviewFailure } from '../api/previewClips'
import type { ClipService } from '../preview/clipService'
import { RenderedClipTransport, type ClipAudio } from '../preview/renderedClipTransport'

// Fakes for the rendered-clip transport's tests: a media element that does only what a test makes it
// do (`fire` stands for the browser's events), a clip service that answers windows of a fixed
// length, and a monotonic clock a test advances together with the 1 s tick.

export class FakeAudio implements ClipAudio {
  src = ''
  currentTime = 0
  preload = ''
  paused = true
  playCalls = 0
  playRejects: Error | null = null
  private readonly listeners = new Map<string, Set<() => void>>()

  play(): Promise<void> {
    this.playCalls += 1
    if (this.playRejects !== null) return Promise.reject(this.playRejects)
    this.paused = false
    return Promise.resolve()
  }

  pause(): void {
    this.paused = true
  }

  load(): void {
    // A real element starts loading `src`; nothing to do here.
  }

  removeAttribute(name: string): void {
    if (name === 'src') this.src = ''
  }

  addEventListener(type: string, listener: () => void): void {
    const set = this.listeners.get(type) ?? new Set<() => void>()
    set.add(listener)
    this.listeners.set(type, set)
  }

  removeEventListener(type: string, listener: () => void): void {
    this.listeners.get(type)?.delete(listener)
  }

  /** The browser reports `type` on this element. */
  fire(type: string): void {
    for (const listener of this.listeners.get(type) ?? []) listener()
  }
}

export const WINDOW_MS = 240_000
export const MIX_END_MS = 1_000_000

export interface Held {
  startMs: number
  release: (clip: Clip) => void
  fail: (error: unknown) => void
}

export class FakeService implements ClipService {
  sessions: string[] = []
  requests: { sessionId: string; startMs: number }[] = []
  createFailure: PreviewFailure | null = null
  /** A clip failure by start. */
  failures = new Map<number, PreviewFailure>()
  /** The starts whose answer a test holds back (`held` lists them). */
  hold = new Set<number>()
  held: Held[] = []
  rendering = new Set<number>()

  createSession(): Promise<string> {
    if (this.createFailure !== null) return Promise.reject(new PreviewError(this.createFailure))
    const id = `s${String(this.sessions.length + 1)}`
    this.sessions.push(id)
    return Promise.resolve(id)
  }

  clip(sessionId: string, startMs: number, _signal: AbortSignal, onRendering: () => void): Promise<Clip> {
    this.requests.push({ sessionId, startMs })
    if (this.rendering.has(startMs)) onRendering()
    const failure = this.failures.get(startMs)
    if (failure !== undefined) return Promise.reject(new PreviewError(failure))
    const clip: Clip = { url: `/clip/${sessionId}/${String(startMs)}`, startMs, endMs: Math.min(startMs + WINDOW_MS, MIX_END_MS) }
    if (!this.hold.has(startMs)) return Promise.resolve(clip)
    return new Promise((resolve, reject) => {
      this.held.push({ startMs, release: resolve, fail: reject })
    })
  }
}

export interface Rig {
  transport: RenderedClipTransport
  service: FakeService
  audio: [FakeAudio, FakeAudio]
  /** The element that is playing (the one `play()` was last called on). */
  current: () => FakeAudio
  /** The clock in ms. */
  clock: () => number
  /** Moves the monotonic clock and the timers on by `ms`, in steps of at most 1 s so each tick sees its own time. */
  advance: (ms: number) => Promise<void>
}

/** Call `vi.useFakeTimers()` first. */
export function rig(extra: { mixEndMs?: number } = {}): Rig {
  let time = 1000
  const audio: [FakeAudio, FakeAudio] = [new FakeAudio(), new FakeAudio()]
  const service = new FakeService()
  const transport = new RenderedClipTransport({ service, audio, now: () => time, mixEndMs: extra.mixEndMs ?? MIX_END_MS })
  return {
    transport,
    service,
    audio,
    current: () => audio.find((a) => !a.paused) ?? audio[0],
    clock: () => time,
    advance: async (ms) => {
      let left = ms
      while (left > 0) {
        const step = Math.min(left, 1000)
        time += step
        await vi.advanceTimersByTimeAsync(step)
        left -= step
      }
    },
  }
}

/** Lets resolved promises run. */
export const flush = async (): Promise<void> => {
  await vi.advanceTimersByTimeAsync(0)
}
