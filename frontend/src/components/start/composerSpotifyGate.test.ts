import { describe, expect, it } from 'vitest'
import type { SongsAvailability, SwedebeatMixAvailability } from '../songs/MusicChoice'
import { composerSpotifyGate } from './composerSpotifyGate'
import { NO_TAGS, addPlaylist, type MusicTags } from './musicTags'

const NEEDS: SongsAvailability = { kind: 'needs_spotify' }
const CONSENT: SongsAvailability = { kind: 'needs_consent', spotifyConnectionId: 's1' }
const READY: SongsAvailability = { kind: 'ready', spotifyConnectionId: 's1' }
const OFF: SongsAvailability = { kind: 'off' }
const NO_MIX: SwedebeatMixAvailability = { kind: 'off' }
const TAGGED = addPlaylist(NO_TAGS, { id: 'p1', name: 'Hills' })
const NO_MUSIC: MusicTags = { kind: 'none' }

describe('the composer start gate', () => {
  it('lets a request with the music left open start without Spotify, and says songs need it', () => {
    expect(composerSpotifyGate(NEEDS, NO_MIX, NO_TAGS, false)).toEqual({ gate: null, songsLater: true })
  })

  it('lets it start without the working-playlist consent: the card comes after the sketch', () => {
    expect(composerSpotifyGate(CONSENT, NO_MIX, NO_TAGS, false)).toEqual({ gate: null, songsLater: false })
  })

  it('still needs the connection for attached playlists, which the server refuses without it', () => {
    expect(composerSpotifyGate(NEEDS, NO_MIX, TAGGED, false).gate).toEqual({ need: 'connect', blocking: true })
    expect(composerSpotifyGate(CONSENT, NO_MIX, TAGGED, false).gate).toEqual({ need: 'consent', blocking: true })
  })

  it('keeps the gate when song picking is not available', () => {
    expect(composerSpotifyGate(OFF, NO_MIX, NO_TAGS, false).gate).toEqual({ need: 'unavailable', blocking: true })
  })

  it('has nothing to say with Spotify ready, for "No music" or for a running class', () => {
    expect(composerSpotifyGate(READY, NO_MIX, NO_TAGS, false)).toEqual({ gate: null, songsLater: false })
    expect(composerSpotifyGate(NEEDS, NO_MIX, NO_MUSIC, false)).toEqual({ gate: null, songsLater: false })
    expect(composerSpotifyGate(NEEDS, NO_MIX, NO_TAGS, true)).toEqual({ gate: null, songsLater: false })
  })
})
