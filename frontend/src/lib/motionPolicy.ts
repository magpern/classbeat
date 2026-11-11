// When the landing page moves, and when it holds still. Pure rules: no DOM, no window.

/** Share of the viewport kept clear at the bottom before a block counts as "in view". */
export const REVEAL_BOTTOM_MARGIN = '0px 0px -6% 0px'

/** The reveal transition, in ms (motion.css uses the same figure; a test keeps the two equal). */
export const REVEAL_MS = 900

/**
 * A block starts hidden, to fade in on scroll, only when it lies fully below the first screen.
 * One that is already on screen, or that the reader has scrolled past (a reload that restored the
 * scroll position), is left as it is: nothing may blink away just to blink back.
 */
export function startsHidden(top: number, viewportHeight: number): boolean {
  return top >= viewportHeight
}

/** Milliseconds to wait before a revealed block drops its reveal attribute (its transition is over). */
export function revealSettleMs(delayMs: number): number {
  return REVEAL_MS + Math.max(0, delayMs) + 100
}
