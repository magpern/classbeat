import { afterEach, describe, expect, it } from 'vitest'
import { MAX_REQUEST, addPhrase, recallRequest, rememberRequest, startAgainPath } from './startRequest'

afterEach(() => sessionStorage.clear())

describe('addPhrase', () => {
  it('appends a phrase once and separates it from what was typed', () => {
    expect(addPhrase('', 'Add climbs.')).toBe('Add climbs.')
    expect(addPhrase('A 45 minute ride.', 'Add climbs.')).toBe('A 45 minute ride. Add climbs.')
    expect(addPhrase('A ride. Add climbs.', 'Add climbs.')).toBe('A ride. Add climbs.')
  })

  it('never grows past the length the server accepts', () => {
    const full = 'x'.repeat(MAX_REQUEST - 3)
    expect(addPhrase(full, 'Add climbs.')).toBe(full)
  })
})

describe('the kept request', () => {
  it('is remembered per class and opens the start screen again', () => {
    rememberRequest('c1', { request: 'A hilly ride', music: null, sport: 'cycling' })
    expect(recallRequest('c1')?.request).toBe('A hilly ride')
    expect(recallRequest('c2')).toBeNull()
    expect(startAgainPath('c1')).toBe('/classes/new?from=c1')
  })
})
