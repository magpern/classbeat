import type { Clip } from '../api/previewClips'
import { STOP_EVENTS } from './listenTimer'

// The two `<audio>` elements of the rendered-clip transport (ADR 0042 section 6.4): one plays, the
// other holds the next window. No Web Audio and no Media Source Extensions: only the elements.

/** The subset of `HTMLAudioElement` the transport uses; a test fakes it. */
export interface ClipAudio {
  src: string
  currentTime: number
  readonly paused: boolean
  preload: string
  play(): Promise<void>
  pause(): void
  load(): void
  removeAttribute(name: string): void
  addEventListener(type: string, listener: () => void): void
  removeEventListener(type: string, listener: () => void): void
}

export type Slot = 0 | 1

/** One window held by an element. */
export interface Win {
  clip: Clip
  slot: Slot
  /** The draft's audio changed after the clip was fetched: it is not played from again. */
  stale: boolean
}

const EVENTS = ['playing', 'timeupdate', 'error', ...STOP_EVENTS]

export class ClipSlots {
  active: Slot = 0
  private readonly wired: { slot: Slot; type: string; handler: () => void }[] = []
  private readonly els: readonly [ClipAudio, ClipAudio]

  /** `onMedia` hears the events of both elements; the caller ignores those of the one that is not active. */
  constructor(els: readonly [ClipAudio, ClipAudio], onMedia: (slot: Slot, type: string) => void) {
    this.els = els
    for (const slot of [0, 1] as const) {
      for (const type of EVENTS) {
        const handler = () => {
          onMedia(slot, type)
        }
        els[slot].addEventListener(type, handler)
        this.wired.push({ slot, type, handler })
      }
    }
  }

  get activeEl(): ClipAudio {
    return this.els[this.active]
  }

  el(win: Win): ClipAudio {
    return this.els[win.slot]
  }

  /** The clip is loaded in the element that is not playing. */
  place(clip: Clip): Win {
    const slot: Slot = this.active === 0 ? 1 : 0
    this.els[slot].preload = 'auto'
    this.els[slot].src = clip.url
    return { clip, slot, stale: false }
  }

  /** Where the window's element is, in class ms. */
  positionOf(win: Win): number {
    return Math.round(win.clip.startMs + this.els[win.slot].currentTime * 1000)
  }

  /** Lets go of one element's clip. */
  release(slot: Slot): void {
    this.els[slot].pause()
    this.els[slot].removeAttribute('src')
    this.els[slot].load()
  }

  releaseAll(): void {
    this.release(0)
    this.release(1)
  }

  dispose(): void {
    for (const { slot, type, handler } of this.wired) this.els[slot].removeEventListener(type, handler)
  }
}

/** The page's subscription to a transport: called after every change of what `observe()` would say. */
export class Changes {
  private readonly listeners = new Set<() => void>()

  subscribe(listener: () => void): () => void {
    this.listeners.add(listener)
    return () => {
      this.listeners.delete(listener)
    }
  }

  emit(): void {
    for (const listener of this.listeners) listener()
  }

  clear(): void {
    this.listeners.clear()
  }
}
