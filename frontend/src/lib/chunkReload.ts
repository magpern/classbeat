// A page chunk that fails to load (a network change aborts the request, or a deploy replaced the
// chunk) would leave the app blank: React.lazy keeps the rejected import. Vite announces the
// failure as `vite:preloadError`; the page then reloads once, which fetches the chunk (or the new
// index.html) again. A second failure within RELOAD_WINDOW_MS is left to surface, and without
// session storage there is no reload at all, so a chunk that is really gone never loops.

export const RELOAD_KEY = 'classbeat.chunkReloadAt'
export const RELOAD_WINDOW_MS = 10_000

type Store = Pick<Storage, 'getItem' | 'setItem'>

/** Whether a failed chunk should reload the page now; records the reload when it should. */
export function shouldReload(storage: () => Store | null, now: number): boolean {
  try {
    const store = storage()
    if (store === null) return false
    const last = Number(store.getItem(RELOAD_KEY))
    if (store.getItem(RELOAD_KEY) !== null && now - last >= 0 && now - last < RELOAD_WINDOW_MS) return false
    store.setItem(RELOAD_KEY, String(now))
    return true
  } catch {
    return false // storage blocked: no reload guard, so no reload
  }
}

export function installChunkReload(deps: {
  target: Pick<Window, 'addEventListener'>
  storage: () => Store | null
  reload: () => void
  now: () => number
}): void {
  deps.target.addEventListener('vite:preloadError', (event) => {
    if (!shouldReload(deps.storage, deps.now())) return
    event.preventDefault()
    deps.reload()
  })
}
