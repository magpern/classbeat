import { describe, expect, it } from 'vitest'
import { DEFAULT_OUTPUTS, toggleOutput } from './studioOutputs'

describe('the output choice', () => {
  it('starts with Spotify and the SwedeBeat playlist', () => {
    expect(DEFAULT_OUTPUTS).toEqual(['spotify_playlist', 'swedebeat_playlist'])
  })

  it('MOWL on adds the mix, and the mix cannot go while MOWL is on', () => {
    const withMowl = toggleOutput([...DEFAULT_OUTPUTS], 'mowl', true)
    expect(withMowl).toEqual(['spotify_playlist', 'swedebeat_playlist', 'swedebeat_mix', 'mowl'])
    expect(toggleOutput(withMowl, 'swedebeat_mix', false)).toEqual(['spotify_playlist', 'swedebeat_playlist'])
  })

  it('keeps the canonical order and never repeats an output', () => {
    expect(toggleOutput(['swedebeat_playlist'], 'spotify_playlist', true)).toEqual(['spotify_playlist', 'swedebeat_playlist'])
    expect(toggleOutput(['spotify_playlist'], 'spotify_playlist', true)).toEqual(['spotify_playlist'])
  })
})
