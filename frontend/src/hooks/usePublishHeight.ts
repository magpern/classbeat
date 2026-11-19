import { useLayoutEffect, type RefObject } from 'react'

/**
 * Publishes the height of a fixed bar as a custom property on the root element (a CSSOM write, which
 * the `style-src 'self'` policy allows, ADR 0030), so the rest of the page can leave room for it:
 * `--cb-tabbar-h` for the phone tab bar, `--cb-notice-measured` for the cookie notice (app.css). It is
 * set before the first paint, follows the bar when its text wraps differently and is removed on
 * unmount. Nothing runs at build time (an effect never runs in a prerender) and a browser without
 * `ResizeObserver` keeps the first reading.
 */
export function usePublishHeight(ref: RefObject<HTMLElement | null>, property: string): void {
  useLayoutEffect(() => {
    const element = ref.current
    if (element === null) return
    const root = document.documentElement
    const publish = () => {
      root.style.setProperty(property, `${String(element.offsetHeight)}px`)
    }
    publish()
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(publish)
    observer?.observe(element, { box: 'border-box' })
    return () => {
      observer?.disconnect()
      root.style.removeProperty(property)
    }
  }, [ref, property])
}
