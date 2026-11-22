import { isAbort, reasonOf, type Clip } from '../api/previewClips'
import { Changes, ClipSlots, type ClipAudio, type Slot, type Win } from './clipSlots'
import type { ClipService } from './clipService'
import { ListenSession } from './listenSession'
import type { Ack, AuditionRequest, PreviewTransport, TransportCapabilities, TransportState } from './transport'
import { isLast, viewOf, wantsPreload, windowAt } from './windowPolicy'

// The SwedeBeat audition's transport (ADR 0042 sections 2, 6.3, 6.4): the server's rendered windows in
// two `<audio>` elements (`ClipSlots`); no Web Audio, no gapless technique. The next window preloads in
// the idle element and playback switches on `ended`; the short gap is accepted (R1: no start offset).
// A session lives from Play to Pause (`ListenSession`): `audition` starts one when there is none and
// otherwise seeks inside it; a seek, a track change or an edit never resets its counter.

export type { ClipAudio } from './clipSlots'

export interface RenderedClipOptions {
  service: ClipService
  audio: readonly [ClipAudio, ClipAudio]
  /** The monotonic clock in ms (`performance.now`), injected. */
  now: () => number
  /** The mix's length in ms: the class ends there. */
  mixEndMs: number
  /** True only when the chosen outputs include the mix. */
  exactMix?: boolean
}

type Rest = { status: 'idle' | 'paused' | 'error'; reason?: string }

export class RenderedClipTransport implements PreviewTransport {
  readonly capabilities: TransportCapabilities
  private readonly service: ClipService
  private readonly slots: ClipSlots
  private readonly now: () => number
  private readonly listen: ListenSession
  private readonly changes = new Changes()
  private mixEndMs: number
  private cur: Win | null = null
  private next: Win | null = null
  private loadingNext = false
  private nextFailure: string | null = null
  private pastEnd = false
  private waitingForNext = false
  private loading: { at: number; rendering: boolean } | null = null
  private problem: string | null = null
  private rest: Rest = { status: 'idle' }
  private lastMs = 0
  private piece: number | undefined
  private gen = 0
  private abort = new AbortController()

  constructor(options: RenderedClipOptions) {
    this.service = options.service
    this.now = options.now
    this.mixEndMs = options.mixEndMs
    this.listen = new ListenSession(options.service, options.now, () => {
      this.endSession({ status: 'paused', reason: 'limit' })
    })
    this.slots = new ClipSlots(options.audio, (slot, type) => {
      this.onMedia(slot, type)
    })
    this.capabilities = { exactMix: options.exactMix ?? false, crossfades: true, seekGranularityMs: 100, kind: 'rendered' }
  }

  /** Called after every change of what `observe()` would say; the page reads `observe()` then. */
  subscribe(listener: () => void): () => void {
    return this.changes.subscribe(listener)
  }

  setMixEnd(ms: number): void {
    this.mixEndMs = ms
  }

  /** The listening counter's total. */
  listenedMs(): number {
    return this.listen.timer.elapsedMs()
  }

  /** Play (no session: a new one, the counter at 0) or a seek (a session: the same one, the counter kept). */
  async audition(request: AuditionRequest): Promise<Ack> {
    const { classMs } = request
    if (!Number.isFinite(classMs) || classMs < 0 || classMs >= this.mixEndMs) return { ok: false, reason: 'outside_class' }
    this.piece = request.piece
    const gen = this.restart()
    this.problem = null
    if (this.listen.current === null) {
      this.loading = { at: classMs, rendering: false }
      this.notify()
      try {
        await this.listen.ensure(() => gen === this.gen)
      } catch (error) {
        return this.failed(error, gen, classMs)
      }
      if (gen !== this.gen) return { ok: false, reason: 'superseded' }
    }
    return this.playFrom(classMs, gen, false)
  }

  /** A manual Pause: it ends the session. The position stays for the next Play. */
  pause(): Promise<void> {
    if (this.listen.current !== null || this.listen.starting || this.loading !== null) this.endSession({ status: 'paused' })
    return Promise.resolve()
  }

  observe(): TransportState {
    return viewOf({
      observedAt: this.now(),
      piece: this.piece,
      positionMs: this.positionMs(),
      problem: this.problem,
      hasSession: this.listen.current !== null || this.listen.starting,
      rest: this.rest,
      listening: this.listen.timer.running,
      hasWindow: this.cur !== null,
      loading: this.loading,
      waitingForNext: this.waitingForNext,
      elementPaused: this.slots.activeEl.paused,
    })
  }

  /** The draft's AUDIO changed: the clips are stale. Session and counter are kept; the position is fetched again. */
  invalidate(): void {
    if (this.cur !== null) this.cur.stale = true
    this.next = null
    if (this.listen.current === null) return
    const gen = this.restart()
    void this.playFrom(this.positionMs(), gen, true)
  }

  /** Ends the session and lets go of the elements. */
  dispose(): void {
    this.endSession({ status: 'idle' })
    this.slots.dispose()
    this.changes.clear()
  }

  // The session ------------------------------------------------------------------------------

  private restart(): number {
    this.abort.abort()
    this.abort = new AbortController()
    this.loadingNext = false
    this.nextFailure = null
    this.waitingForNext = false
    return ++this.gen
  }

  private endSession(rest: Rest, at: number = this.positionMs()): void {
    this.lastMs = at
    this.restart()
    this.listen.end()
    this.cur = null
    this.next = null
    this.loading = null
    this.problem = null
    this.pastEnd = false
    this.slots.releaseAll()
    this.rest = rest
    this.notify()
  }

  /** Playback stops with a reason the page words; session and counter stay, so Retry is a seek (`at`: where it is for). */
  private stall(reason: string, at?: number): void {
    this.lastMs = at ?? this.positionMs()
    if (at !== undefined) this.cur = null
    this.slots.activeEl.pause()
    this.listen.timer.stop()
    this.loading = null
    this.waitingForNext = false
    this.problem = reason
    this.notify()
  }

  private failed(error: unknown, gen: number, at: number): Ack {
    if (gen !== this.gen || isAbort(error)) return { ok: false, reason: 'superseded' }
    const reason = reasonOf(error)
    if (reason === 'past_end') this.endSession({ status: 'idle' }, 0)
    else if (reason === 'session_ended') this.endSession({ status: 'paused', reason })
    else if (this.listen.current === null || reason === 'unavailable') this.endSession({ status: 'error', reason }, at)
    else this.stall(reason, at)
    return { ok: false, reason }
  }

  // Windows ----------------------------------------------------------------------------------

  private positionMs(): number {
    if (this.loading !== null) return this.loading.at
    return this.cur === null ? this.lastMs : this.slots.positionOf(this.cur)
  }

  private async playFrom(classMs: number, gen: number, keepPlaying: boolean): Promise<Ack> {
    const session = this.listen.current
    if (session === null) return { ok: false, reason: 'superseded' }
    const here = windowAt([this.cur, this.next], classMs)
    if (here !== null) {
      const offset = (classMs - here.clip.startMs) / 1000
      if (here === this.cur) {
        this.slots.el(here).currentTime = offset
        this.run(this.slots.el(here))
      } else this.switchTo(here, offset)
      this.loading = null
      this.notify()
      return { ok: true }
    }
    this.next = null
    if (!keepPlaying) this.slots.activeEl.pause()
    this.loading = { at: classMs, rendering: false }
    this.notify()
    let clip: Clip
    try {
      clip = await this.service.clip(session, classMs, this.abort.signal, () => {
        if (gen !== this.gen || this.loading === null) return
        this.loading.rendering = true
        this.notify()
      })
    } catch (error) {
      return this.failed(error, gen, classMs)
    }
    if (gen !== this.gen) return { ok: false, reason: 'superseded' }
    this.switchTo(this.slots.place(clip), (classMs - clip.startMs) / 1000)
    return { ok: true }
  }

  /** The window becomes the active one. The counter stops here, so the gap is not counted, and restarts on `playing`. */
  private switchTo(win: Win, offsetSec: number): void {
    this.listen.timer.stop()
    if (win.slot !== this.slots.active) this.slots.release(this.slots.active)
    this.slots.active = win.slot
    this.cur = win
    if (this.next === win) this.next = null
    this.waitingForNext = false
    this.loading = null
    this.problem = null
    this.slots.el(win).currentTime = offsetSec
    this.run(this.slots.el(win))
    this.notify()
  }

  private run(el: ClipAudio): void {
    el.play().catch((error: unknown) => {
      if (isAbort(error) || el !== this.slots.activeEl || this.listen.current === null) return
      this.stall('blocked')
    })
  }

  private startPreload(): void {
    const { cur } = this
    const session = this.listen.current
    if (cur === null || session === null || this.loadingNext || this.next !== null) return
    this.loadingNext = true
    const gen = this.gen
    this.service.clip(session, cur.clip.endMs, this.abort.signal, () => undefined).then(
      (clip) => {
        if (gen !== this.gen) return
        this.loadingNext = false
        this.next = this.slots.place(clip)
        if (this.waitingForNext) this.switchTo(this.next, 0)
      },
      (error: unknown) => {
        if (gen !== this.gen || isAbort(error)) return
        this.loadingNext = false
        const reason = reasonOf(error)
        if (reason === 'session_ended' || reason === 'unavailable') {
          this.endSession({ status: reason === 'unavailable' ? 'error' : 'paused', reason })
          return
        }
        if (reason === 'past_end') this.pastEnd = true
        else this.nextFailure = reason
        if (this.waitingForNext) this.advance()
      },
    )
  }

  // The media events, of the active element only ----------------------------------------------

  private onMedia(slot: Slot, type: string): void {
    if (slot !== this.slots.active || this.listen.current === null || this.cur === null) return
    if (type === 'playing') {
      this.listen.timer.start()
      this.notify()
    } else if (type === 'timeupdate') {
      const idle = this.next === null && this.nextFailure === null && !this.pastEnd
      if (idle && wantsPreload(this.cur.clip, this.positionMs(), this.mixEndMs)) this.startPreload()
    } else {
      this.listen.timer.stop()
      if (type === 'ended') this.advance()
      this.notify()
    }
  }

  /** The playing window is over: the next one, the end of the class, or what went wrong with the next one. */
  private advance(): void {
    const cur = this.cur
    if (cur === null || this.loading !== null) return
    if (isLast(cur.clip, this.mixEndMs) || this.pastEnd) this.endSession({ status: 'idle' }, 0)
    else if (this.next !== null) this.switchTo(this.next, 0)
    else if (this.nextFailure !== null) this.stall(this.nextFailure, cur.clip.endMs)
    else {
      this.waitingForNext = true
      this.startPreload()
    }
  }

  private notify(): void {
    this.changes.emit()
  }
}
