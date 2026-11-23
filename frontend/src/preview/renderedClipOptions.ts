import type { ClipAudio } from './clipSlots'
import type { ClipService } from './clipService'

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
