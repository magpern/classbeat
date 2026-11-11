import { useEffect, useRef } from 'react'
import { HERO_BPM } from '../../lib/beatField'
import { reducedMotion } from '../../lib/reducedMotion'
import { useResolvedTheme } from '../../theme/useResolvedTheme'
import { drawBeatField, type BeatFieldLook } from './drawBeatField'

/** A moment in the beat with the bars well up: the one still frame shown when motion is reduced. */
const STILL_SECONDS = 0.18

/** The tokens' colours as the canvas needs them (it cannot read `var(--…)`). */
function readLook(): BeatFieldLook {
  const css = getComputedStyle(document.documentElement)
  const token = (name: string, fallback: string) => css.getPropertyValue(name).trim() || fallback
  return {
    zones: [1, 2, 3, 4, 5].map((z) => token(`--cb-zone-${String(z)}`, 'gray')),
    brand: token('--cb-brand', 'white'),
  }
}

/**
 * The hero's moving backdrop: a flywheel whose bars kick on the beat and climb through the effort
 * zones, with a marker orbiting like a pedal. Decorative (aria-hidden), drawn behind the scrim.
 * It stops when scrolled out of view or when the tab is hidden, leans slightly towards the pointer,
 * and under reduced motion draws one still frame.
 */
export function BeatField() {
  const ref = useRef<HTMLCanvasElement>(null)
  const theme = useResolvedTheme()

  useEffect(() => {
    const canvas = ref.current
    const ctx = canvas?.getContext('2d')
    if (!canvas || !ctx) return
    const look = readLook()
    const still = reducedMotion()
    let width = 0
    let height = 0
    let dpr = 1
    let lean = { x: 0, y: 0 } // pointer target, -0.5 to 0.5
    let leanNow = { x: 0, y: 0 }
    let raf = 0
    let running = false
    const t0 = performance.now()

    const draw = (seconds: number) => {
      const phone = width < 640
      leanNow = { x: leanNow.x + (lean.x - leanNow.x) * 0.05, y: leanNow.y + (lean.y - leanNow.y) * 0.05 }
      drawBeatField(ctx, look, {
        width,
        height,
        cx: width * (phone ? 0.5 : 0.76) + leanNow.x * 22,
        cy: height * (phone ? 0.3 : 0.5) + leanNow.y * 16,
        radius: phone ? Math.min(width * 0.4, height * 0.24) : Math.min(width * 0.2, height * 0.42),
        bars: phone ? 64 : 96,
        seconds,
        bpm: HERO_BPM,
      })
    }
    const resize = () => {
      const box = canvas.getBoundingClientRect()
      dpr = Math.min(window.devicePixelRatio || 1, 2)
      width = box.width
      height = box.height
      canvas.width = Math.round(width * dpr)
      canvas.height = Math.round(height * dpr)
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      if (!running) draw(STILL_SECONDS)
    }
    const loop = (now: number) => {
      draw((now - t0) / 1000)
      if (running) raf = requestAnimationFrame(loop)
    }
    const start = () => {
      if (running || still) return
      running = true
      raf = requestAnimationFrame(loop)
    }
    const stop = () => {
      running = false
      cancelAnimationFrame(raf)
    }
    const onPointer = (event: PointerEvent) => {
      lean = { x: event.clientX / window.innerWidth - 0.5, y: event.clientY / window.innerHeight - 0.5 }
    }
    const onVisibility = () => {
      if (document.hidden) stop()
      else if (onScreen) start()
    }

    let onScreen = true
    const seen =
      typeof IntersectionObserver === 'undefined'
        ? undefined
        : new IntersectionObserver(([entry]) => {
            onScreen = entry?.isIntersecting ?? true
            if (onScreen && !document.hidden) start()
            else stop()
          })
    seen?.observe(canvas)
    const resizer = typeof ResizeObserver === 'undefined' ? undefined : new ResizeObserver(resize)
    resizer?.observe(canvas)
    resize()
    if (!still) {
      window.addEventListener('pointermove', onPointer, { passive: true })
      document.addEventListener('visibilitychange', onVisibility)
      if (!seen) start()
    }
    return () => {
      stop()
      seen?.disconnect()
      resizer?.disconnect()
      window.removeEventListener('pointermove', onPointer)
      document.removeEventListener('visibilitychange', onVisibility)
    }
    // The tokens' colours differ by theme, so a theme change reads them again.
  }, [theme])

  return <canvas ref={ref} className="beat-field" aria-hidden="true" />
}
