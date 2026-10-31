import { vi } from 'vitest'

/** Makes matchMedia answer min-width queries for a window of `width` px (restored after each test). */
export function setWidth(width: number): void {
  vi.stubGlobal('matchMedia', (query: string) => {
    const min = /min-width:\s*(\d+)px/.exec(query)
    return {
      matches: min?.[1] !== undefined && width >= Number(min[1]),
      media: query,
      onchange: null,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      addListener: () => undefined,
      removeListener: () => undefined,
      dispatchEvent: () => false,
    }
  })
}
