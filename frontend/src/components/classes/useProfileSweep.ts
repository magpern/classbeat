import { useEffect, type RefObject } from 'react'
import { reducedMotion } from '../../lib/reducedMotion'

/** viewBox width of MiniProfile: a bar's x divided by this is where in the class it starts. */
const VIEW_WIDTH = 200

/**
 * Readies a MiniProfile for motion (motion.css): each bar learns its place in the row (--i, for the
 * rise) and in the class (--s, for when the playhead lights it). Set through the CSSOM, because the
 * served pages' CSP refuses a `style="…"` attribute. Nothing is set under reduced motion.
 */
export function useProfileSweep(ref: RefObject<SVGSVGElement | null>, enabled: boolean): void {
  useEffect(() => {
    const svg = ref.current
    if (!enabled || !svg || reducedMotion()) return
    svg.querySelectorAll<SVGElement>('.mini-bar').forEach((bar, i) => {
      bar.style.setProperty('--i', String(i))
      bar.style.setProperty('--s', String(Number.parseFloat(bar.getAttribute('x') ?? '0') / VIEW_WIDTH))
    })
    svg.setAttribute('data-live', 'true')
    return () => {
      svg.removeAttribute('data-live')
    }
  }, [ref, enabled])
}
