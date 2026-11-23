import { useEffect, useRef, type RefObject } from 'react'

// A committed edit unmounts the boundary handles and disables the fieldsets while the page is
// busy, which drops keyboard focus to the page. This puts it back on the control that had it
// (or its counterpart in the reloaded page) once the page is idle again.

/** A selector that finds the same control again after a reload; null: it has no stable name. */
export function selectorFor(el: Element): string | null {
  const escape = (v: string) => v.replace(/["\\]/g, '\\$&')
  for (const attr of ['data-handle', 'data-piece', 'data-restore-focus', 'id', 'name', 'aria-label']) {
    const value = el.getAttribute(attr)
    if (value !== null && value !== '') return `[${attr}="${escape(value)}"]`
  }
  return null
}

export function useRestoreFocus(root: RefObject<HTMLElement | null>, busy: boolean): void {
  const last = useRef<{ element: Element; selector: string | null } | null>(null)
  const wasBusy = useRef(false)

  useEffect(() => {
    const node = root.current
    if (node === null) return
    const onFocusIn = (event: FocusEvent) => {
      const target = event.target
      if (target instanceof Element) last.current = { element: target, selector: selectorFor(target) }
    }
    node.addEventListener('focusin', onFocusIn)
    return () => {
      node.removeEventListener('focusin', onFocusIn)
    }
  }, [root])

  useEffect(() => {
    const was = wasBusy.current
    wasBusy.current = busy
    if (busy || !was) return
    const node = root.current
    const remembered = last.current
    if (node === null || remembered === null) return
    const active = document.activeElement
    if (active !== null && active !== document.body && node.contains(active)) return // focus is already somewhere useful
    const target = remembered.element.isConnected ? remembered.element : remembered.selector === null ? null : node.querySelector(remembered.selector)
    if (target instanceof HTMLElement || target instanceof SVGElement) target.focus()
  }, [busy, root])
}
