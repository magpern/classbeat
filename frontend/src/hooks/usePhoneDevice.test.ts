import { afterEach, describe, expect, it, vi } from 'vitest'
import { isPhoneDevice } from './usePhoneDevice'

function device(coarse: boolean, width: number, height: number) {
  vi.stubGlobal('matchMedia', (query: string) => ({ matches: coarse && query.includes('coarse') }))
  vi.stubGlobal('screen', { width, height })
}

describe('isPhoneDevice', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })
  it('is true for a phone held in landscape', () => {
    device(true, 844, 390)
    expect(isPhoneDevice()).toBe(true)
  })
  it('is false for a tablet and for a desktop', () => {
    device(true, 1024, 768)
    expect(isPhoneDevice()).toBe(false)
    device(false, 390, 844)
    expect(isPhoneDevice()).toBe(false)
  })
})
