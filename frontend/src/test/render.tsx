import { render } from '@testing-library/react'
import { App } from '../App'
import { localize } from '../router/paths'

/**
 * Renders the whole app at `url` (path, query and fragment), like a browser opening it. A neutral
 * path is opened under `/en` (ADR 0027), which is what the redirect would do for an English
 * browser; `/admin`, an already prefixed path and `renderAtRaw` are opened as given.
 */
export function renderAt(url: string) {
  return renderAtRaw(localize(url, 'en'))
}

/** Renders the app at exactly `url`, for the unprefixed-URL redirect and the unknown-code tests. */
export function renderAtRaw(url: string) {
  window.history.replaceState(null, '', url)
  return render(<App />)
}

/**
 * A copy of the page without its site footer, for "the page never mentions X" checks: the footer
 * names MOWL, Intelligent Cycling, Spotify and Swedebeat on every page (trademark notice, #1268).
 */
export function withoutFooter(): HTMLElement {
  const copy = document.body.cloneNode(true) as HTMLElement
  copy.querySelectorAll('footer').forEach((footer) => {
    footer.remove()
  })
  return copy
}
