/**
 * What a modal dialog does to the page behind it, without a single inline style (ADR 0013 section
 * 9: `style-src 'self'` refuses the `<style>` element and the style attribute that Radix's own
 * scroll lock writes). The page cannot scroll because the root element gets a utility class, and
 * it cannot be reached because every other child of `<body>` is `inert` (no focus, no clicks,
 * out of the accessibility tree). Both are undone, whatever else changed meanwhile, when the
 * dialog goes; locks nest.
 */
const SCROLL_LOCK_CLASS = 'overflow-hidden'

let locks = 0

/** Lock the page for `dialog` (an element inside a body-level portal); returns the undo. */
export function lockPageBehind(dialog: HTMLElement): () => void {
  const own = dialog.closest('body > *')
  const inerted: Element[] = []
  for (const child of document.body.children) {
    if (child === own || child.hasAttribute('inert')) continue
    child.setAttribute('inert', '')
    inerted.push(child)
  }
  locks += 1
  document.documentElement.classList.add(SCROLL_LOCK_CLASS)
  return () => {
    for (const child of inerted) child.removeAttribute('inert')
    locks -= 1
    if (locks === 0) document.documentElement.classList.remove(SCROLL_LOCK_CLASS)
  }
}

const FOCUSABLE = 'a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])'

/** Tab and Shift+Tab stay inside `dialog`: the last control leads to the first and back. */
export function keepTabInside(event: { key: string; shiftKey: boolean; preventDefault: () => void }, dialog: HTMLElement): void {
  if (event.key !== 'Tab') return
  const stops = [...dialog.querySelectorAll<HTMLElement>(FOCUSABLE)]
  const first = stops[0]
  const last = stops[stops.length - 1]
  if (first === undefined || last === undefined) {
    event.preventDefault()
    return
  }
  const active = document.activeElement
  if (event.shiftKey && (active === first || active === dialog)) {
    event.preventDefault()
    last.focus()
  } else if (!event.shiftKey && active === last) {
    event.preventDefault()
    first.focus()
  }
}
