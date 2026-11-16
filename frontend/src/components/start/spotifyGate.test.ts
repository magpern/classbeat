import { describe, expect, it } from 'vitest'
import type { MusicOverride } from '../../api/interpret'
import type { SongsAvailability, SwedebeatMixAvailability } from '../songs/MusicChoice'
import { spotifyConnectionId, spotifyGate } from './spotifyGate'

const PLAYLIST: MusicOverride = { source: 'mowl_spotify_playlist', spotify_playlist_id: 'p' }
const MIX: MusicOverride = { source: 'mowl_swedebeat_mix', mix_id: 'm' }
const NONE: MusicOverride = { source: 'none' }
const READY: SongsAvailability = { kind: 'ready', spotifyConnectionId: 's1' }
const CONSENT: SongsAvailability = { kind: 'needs_consent', spotifyConnectionId: 's1' }
const NEEDS: SongsAvailability = { kind: 'needs_spotify' }
const OFF: SongsAvailability = { kind: 'off' }
const NO_MIXES: SwedebeatMixAvailability = { kind: 'off' }
const MIXES: SwedebeatMixAvailability = { kind: 'ready' }

describe('spotifyGate', () => {
  const connect = { need: 'connect', blocking: true } as const
  const cases: [string, SongsAvailability, SwedebeatMixAvailability, MusicOverride | null, boolean, unknown][] = [
    ['connected: nothing stops it', READY, NO_MIXES, null, false, null],
    ['not connected, classbeat chooses', NEEDS, NO_MIXES, null, false, connect],
    ['not connected, a playlist', NEEDS, NO_MIXES, PLAYLIST, false, connect],
    ['not connected, a playlist and mixes on', NEEDS, MIXES, PLAYLIST, false, connect],
    ['not connected, "No music"', NEEDS, NO_MIXES, NONE, false, null],
    ['not connected, a Swedebeat mix', NEEDS, MIXES, MIX, false, null],
    ['not connected, mixes on: the request may name a mix', NEEDS, MIXES, null, false, { need: 'connect', blocking: false }],
    ['not connected, a running class', NEEDS, NO_MIXES, null, true, null],
    ['connected, working playlist not allowed, request left to classbeat', CONSENT, NO_MIXES, null, false, { need: 'consent', blocking: true }],
    ['connected, working playlist not allowed, a playlist', CONSENT, NO_MIXES, PLAYLIST, false, null],
    ['connected, working playlist not allowed, "No music"', CONSENT, NO_MIXES, NONE, false, null],
    ['Spotify not offered here: the server answers', OFF, NO_MIXES, null, false, null],
  ]
  it.each(cases)('%s', (_name, songs, swedebeat, override, running, expected) => {
    expect(spotifyGate(songs, swedebeat, override, running)).toEqual(expected)
  })
})

describe('spotifyConnectionId', () => {
  it('names the connection when connected, with or without the consent', () => {
    expect(spotifyConnectionId(READY)).toBe('s1')
    expect(spotifyConnectionId(CONSENT)).toBe('s1')
    expect(spotifyConnectionId(NEEDS)).toBeUndefined()
    expect(spotifyConnectionId(OFF)).toBeUndefined()
  })
})
