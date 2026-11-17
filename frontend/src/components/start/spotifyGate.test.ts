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
const UNREACHABLE: SongsAvailability = { kind: 'off', unreachable: true }
const NO_MIXES: SwedebeatMixAvailability = { kind: 'off' }
const MIXES: SwedebeatMixAvailability = { kind: 'ready' }

describe('spotifyGate', () => {
  const connect = { need: 'connect', blocking: true } as const
  const unavailable = { need: 'unavailable', blocking: true } as const
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
    // Song picking is not available to the user (the Spotify page is hidden): nothing to connect.
    ['song picking unavailable, classbeat chooses', OFF, NO_MIXES, null, false, unavailable],
    ['song picking unavailable, a playlist', OFF, NO_MIXES, PLAYLIST, false, unavailable],
    ['song picking unavailable, a playlist and mixes on', OFF, MIXES, PLAYLIST, false, unavailable],
    ['song picking unavailable, "No music"', OFF, NO_MIXES, NONE, false, null],
    ['song picking unavailable, a Swedebeat mix', OFF, MIXES, MIX, false, null],
    ['song picking unavailable, mixes on: the request may name a mix', OFF, MIXES, null, false, { need: 'unavailable', blocking: false }],
    ['song picking unavailable, a running class', OFF, NO_MIXES, null, true, null],
    ['status could not be read: the server answers', UNREACHABLE, NO_MIXES, null, false, null],
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
    expect(spotifyConnectionId(UNREACHABLE)).toBeUndefined()
  })
})
