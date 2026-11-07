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
