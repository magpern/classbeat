import { describe, expect, it } from 'vitest'
import type { SongsAvailability } from '../songs/MusicChoice'
import { NO_TAGS, addPlaylist, gateOverride, type MusicTags } from './musicTags'
import { spotifyGate } from './spotifyGate'

// In the composer every class but "No music" is a song pick for the Spotify gate (it may search beyond the
// attached playlists), so attached playlists need the connection AND the working-playlist consent.

const NEEDS: SongsAvailability = { kind: 'needs_spotify' }
const CONSENT: SongsAvailability = { kind: 'needs_consent', spotifyConnectionId: 's1' }
const READY: SongsAvailability = { kind: 'ready', spotifyConnectionId: 's1' }

const NO_MUSIC: MusicTags = { kind: 'none' }
const TAGGED = addPlaylist(NO_TAGS, { id: 'p1', name: 'Hills' })

const connect = { need: 'connect', blocking: true } as const
const consent = { need: 'consent', blocking: true } as const

describe('the composer Spotify gate', () => {
  const cases: [string, SongsAvailability, string, MusicTags, unknown][] = [
    ['no Spotify, no tags', NEEDS, 'no tags', NO_TAGS, connect],
    ['no Spotify, tags', NEEDS, 'tags', TAGGED, connect],
    ['no Spotify, "No music"', NEEDS, 'No music', NO_MUSIC, null],
    ['needs consent, no tags', CONSENT, 'no tags', NO_TAGS, consent],
    ['needs consent, tags', CONSENT, 'tags', TAGGED, consent],
    ['needs consent, "No music"', CONSENT, 'No music', NO_MUSIC, null],
    ['ready, no tags', READY, 'no tags', NO_TAGS, null],
    ['ready, tags', READY, 'tags', TAGGED, null],
    ['ready, "No music"', READY, 'No music', NO_MUSIC, null],
  ]
  it.each(cases)('%s', (_name, songs, _tags, state, expected) => {
    expect(spotifyGate(songs, { kind: 'off' }, gateOverride(state), false)).toEqual(expected)
  })

  it('does not gate a running class, which has playlists only', () => {
    expect(spotifyGate(CONSENT, { kind: 'off' }, gateOverride(TAGGED), true)).toBeNull()
  })
})
