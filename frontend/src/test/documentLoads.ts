import { setDocumentLoader } from '../router/Router'

/**
 * jsdom cannot load a page, so a move the router makes as a real page load is recorded here and the
 * address is set as the browser would leave it. The app is not remounted: a test that needs the new
 * document opens it again.
 */
export function recordDocumentLoads(): string[] {
  const loads: string[] = []
  setDocumentLoader((target, replace) => {
    loads.push(target)
    if (replace) window.history.replaceState(null, '', target)
    else window.history.pushState(null, '', target)
  })
  return loads
}

export function restoreDocumentLoads(): void {
  setDocumentLoader(null)
}
