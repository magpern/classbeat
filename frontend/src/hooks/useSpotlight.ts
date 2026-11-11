import { useEffect, type RefObject } from 'react'

/**
 * A soft light that follows the pointer over any `.glow-card` inside the container (motion.css
 * paints it from --mx and --my). One listener on the container, not one per card. Touch and pen
 * never hover, and the light is only shown under `hover: hover`, so nothing happens for them.
 */
export function useSpotlight(ref: RefObject<HTMLElement | null>): void {
  useEffect(() => {
    const root = ref.current
    if (!root) return
    const onMove = (event: PointerEvent) => {
      if (!(event.target instanceof Element)) return
      const card = event.target.closest('.glow-card')
      if (!(card instanceof HTMLElement)) return
      const box = card.getBoundingClientRect()
      card.style.setProperty('--mx', `${String(event.clientX - box.left)}px`)
      card.style.setProperty('--my', `${String(event.clientY - box.top)}px`)
    }
    root.addEventListener('pointermove', onMove, { passive: true })
    return () => {
      root.removeEventListener('pointermove', onMove)
    }
  }, [ref])
}
