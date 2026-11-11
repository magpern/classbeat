import { afterEach, describe, expect, it, vi } from 'vitest'
import { withViewTransition } from './viewTransition'

afterEach(() => {
  vi.unstubAllGlobals()
  Reflect.deleteProperty(document, 'startViewTransition')
})

describe('withViewTransition', () => {
  it('just moves where the browser has no view transitions', () => {
    const move = vi.fn()
    withViewTransition(move)
    expect(move).toHaveBeenCalledTimes(1)
  })

  it('moves inside the transition where it has them', () => {
    const move = vi.fn()
    const start = vi.fn((update: () => void) => {
      expect(move).not.toHaveBeenCalled()
      update()
    })
    Object.defineProperty(document, 'startViewTransition', { value: start, configurable: true })
    withViewTransition(move)
    expect(start).toHaveBeenCalledTimes(1)
    expect(move).toHaveBeenCalledTimes(1)
  })

  it('skips the transition for a visitor who asked for less motion', () => {
    const start = vi.fn()
    Object.defineProperty(document, 'startViewTransition', { value: start, configurable: true })
    vi.stubGlobal('matchMedia', () => ({ matches: true }))
    const move = vi.fn()
    withViewTransition(move)
    expect(start).not.toHaveBeenCalled()
    expect(move).toHaveBeenCalledTimes(1)
  })
})
