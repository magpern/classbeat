import { useEffect, type RefObject } from 'react'
import { REVEAL_BOTTOM_MARGIN, revealSettleMs, startsHidden } from '../lib/motionPolicy'
import { reducedMotion } from '../lib/reducedMotion'

/** Any element, HTML or SVG, that can carry an inline custom property. */
export type Revealable = Element & ElementCSSInlineStyle

/**
 * Fades a block in (rises, unblurs) the first time it scrolls into view. The hidden state is an
 * attribute this effect sets in the browser, so the prerendered page, a visitor without script and
 * a visitor who asked for less motion all see the block at once. `delayMs` staggers siblings.
 * The delay goes through the CSSOM (`style.setProperty`), which the CSP allows, unlike a
 * `style="…"` attribute in the served HTML.
 */
export function useReveal(ref: RefObject<Revealable | null>, delayMs = 0, enabled = true): void {
  useEffect(() => {
    const el = ref.current
    if (!enabled || !el || reducedMotion() || typeof IntersectionObserver === 'undefined') return
    if (!startsHidden(el.getBoundingClientRect().top, window.innerHeight)) return

    el.style.setProperty('--reveal-delay', `${String(delayMs)}ms`)
    el.setAttribute('data-reveal', 'hidden')
    let settle: ReturnType<typeof setTimeout> | undefined
    const observer = new IntersectionObserver(
      (entries) => {
        if (!entries.some((entry) => entry.isIntersecting)) return
        observer.disconnect()
        el.setAttribute('data-reveal', 'in')
        // Once the transition is over the attribute goes, so it cannot hold back a hover effect.
        settle = setTimeout(() => {
          el.removeAttribute('data-reveal')
        }, revealSettleMs(delayMs))
      },
      { rootMargin: REVEAL_BOTTOM_MARGIN, threshold: 0.08 },
    )
    observer.observe(el)
    return () => {
      observer.disconnect()
      clearTimeout(settle)
      el.removeAttribute('data-reveal')
      el.style.removeProperty('--reveal-delay')
    }
  }, [ref, delayMs, enabled])
}
