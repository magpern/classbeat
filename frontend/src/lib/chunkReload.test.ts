import { describe, expect, it, vi } from 'vitest'
import { MAX_RELOADS, RELOAD_KEY, RELOAD_WINDOW_MS, installChunkReload } from './chunkReload'

// A failed page chunk (`vite:preloadError`) reloads the page once instead of leaving it blank; a
// second failure inside the window, or no session storage, surfaces the error instead.

function memoryStore() {
  const items = new Map<string, string>()
  return {
    getItem: (key: string) => items.get(key) ?? null,
    setItem: (key: string, value: string) => {
      items.set(key, value)
    },
  }
}

function setup(storage: () => ReturnType<typeof memoryStore> | null) {
  const target = new EventTarget()
  const reload = vi.fn()
  let clock = 1_000_000
  installChunkReload({
    target,
    storage,
    reload,
    now: () => clock,
  })
  const fail = () => {
    const event = new Event('vite:preloadError', { cancelable: true })
    target.dispatchEvent(event)
    return event.defaultPrevented
  }
  return {
    reload,
    fail,
    advance: (ms: number) => {
      clock += ms
    },
  }
}

describe('installChunkReload', () => {
  it('reloads once on a failed chunk and handles the error', () => {
    const store = memoryStore()
    const page = setup(() => store)
    expect(page.fail()).toBe(true)
    expect(page.reload).toHaveBeenCalledTimes(1)
    expect(store.getItem(RELOAD_KEY)).toBe('1000000')
  })

  it('lets a second failure inside the window surface, and reloads again after it', () => {
    const store = memoryStore()
    const page = setup(() => store)
    page.fail()
    page.advance(RELOAD_WINDOW_MS - 1)
    expect(page.fail()).toBe(false)
    expect(page.reload).toHaveBeenCalledTimes(1)
    page.advance(1)
    expect(page.fail()).toBe(true)
    expect(page.reload).toHaveBeenCalledTimes(2)
  })

  it('never reloads without session storage', () => {
    for (const storage of [
      () => null,
      () => {
        throw new Error('blocked')
      },
    ]) {
      const page = setup(storage)
      expect(page.fail()).toBe(false)
      expect(page.reload).not.toHaveBeenCalled()
    }
  })

  it('stops reloading after MAX_RELOADS in one session, however far apart the failures are', () => {
    const store = memoryStore()
    const page = setup(() => store)
    for (let i = 0; i < MAX_RELOADS; i += 1) {
      expect(page.fail()).toBe(true)
      page.advance(RELOAD_WINDOW_MS)
    }
    expect(page.fail()).toBe(false)
    expect(page.reload).toHaveBeenCalledTimes(MAX_RELOADS)
  })
})
