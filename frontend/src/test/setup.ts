import '@testing-library/jest-dom/vitest'
import { cleanup, configure } from '@testing-library/react'
import { afterEach } from 'vitest'

// The first render of the whole app in a test file is slow on a loaded machine (cold JIT, CI
// runners): findBy* and waitFor give it 5 s instead of 1 s. Nothing passes sooner or later
// because of this; it only stops a slow start from failing an assertion that would hold.
configure({ asyncUtilTimeout: 5000 })

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
    configurable: true,
    writable: true,
  })
}
