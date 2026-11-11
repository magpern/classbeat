import { flushSync } from 'react-dom'

/**
 * Runs a move to another page inside a view transition, where the browser has them: the old page
 * cross-fades into the new one (motion-app.css sets the pace). The browser fades two pictures of
 * the page, not the page itself, so text is never half-transparent in the document.
 *
 * Only for a move the visitor started (a click on a `Link`): `flushSync` may not run inside a
 * render or an effect. Without the API, or when the visitor asked for less motion, the move just runs.
 */
export function withViewTransition(move: () => void): void {
  const calm = typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches
  if (calm || typeof document.startViewTransition !== 'function') {
    move()
    return
  }
  document.startViewTransition(() => {
    // The new page must be in the document when this returns: that is the picture faded to.
    flushSync(move)
  })
}
