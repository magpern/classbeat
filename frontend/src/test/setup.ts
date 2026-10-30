import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

// Vitest globals are off, so Testing Library cannot register its own cleanup.
afterEach(() => {
  cleanup()
})

// jsdom does not implement scrolling.
window.scrollTo = () => undefined

// jsdom lacks what Radix primitives use for positioning and pointer handling.
const noop = (): void => undefined
class ResizeObserverStub {
  observe = noop
  unobserve = noop
  disconnect = noop
}
const elementShims: Record<string, unknown> = {
  hasPointerCapture: () => false,
  setPointerCapture: noop,
  releasePointerCapture: noop,
  scrollIntoView: noop,
}
for (const [name, fn] of Object.entries(elementShims)) {
  if (!(name in Element.prototype)) Object.defineProperty(Element.prototype, name, { value: fn, configurable: true })
}
if (!('ResizeObserver' in window)) Object.defineProperty(window, 'ResizeObserver', { value: ResizeObserverStub })

// jsdom has no matchMedia (sonner reads it).
if (!('matchMedia' in window)) {
  Object.defineProperty(window, 'matchMedia', {
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: noop,
      removeEventListener: noop,
      addListener: noop,
      removeListener: noop,
      dispatchEvent: () => false,
    }),
  })
}
