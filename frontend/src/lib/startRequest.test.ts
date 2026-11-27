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

  it('keeps the composer\'s playlist ids, and drops anything that is not an id', () => {
    rememberRequest('c1', { request: 'A ride', music: 'playlist:a', playlists: ['a', 'b'], sport: 'cycling' })
    expect(recallRequest('c1')?.playlists).toEqual(['a', 'b'])
    sessionStorage.setItem('classbeat.start-request.c3', JSON.stringify({ request: 'x', playlists: ['a', 3, null] }))
    expect(recallRequest('c3')?.playlists).toEqual(['a'])
    expect(recallRequest('c3')).not.toBeNull()
    rememberRequest('c4', { request: 'Old', music: null, sport: 'cycling' })
    expect(recallRequest('c4')).not.toHaveProperty('playlists')
  })

  it('keeps that the sport was picked by hand, and only when it was (#1562)', () => {
    rememberRequest('c5', { request: 'A run', music: null, sport: 'running', manual: true })
    expect(recallRequest('c5')?.manual).toBe(true)
    rememberRequest('c6', { request: 'A run', music: null, sport: 'running' })
    expect(recallRequest('c6')).not.toHaveProperty('manual')
  })
})
