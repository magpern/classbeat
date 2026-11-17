import { describe, expect, it } from 'vitest'
import type { SongsAvailability, SwedebeatMixAvailability } from '../songs/MusicChoice'
import { NO_TAGS, addPlaylist, gateOverride, type MusicTags } from './musicTags'
import { spotifyGate } from './spotifyGate'

// In the composer every class but "No music" is a song pick for the Spotify gate (it may search beyond the
// attached playlists), so attached playlists or none need the connection AND the working-playlist consent, with SwedeBeat on or off.

const NEEDS: SongsAvailability = { kind: 'needs_spotify' }
const CONSENT: SongsAvailability = { kind: 'needs_consent', spotifyConnectionId: 's1' }
const READY: SongsAvailability = { kind: 'ready', spotifyConnectionId: 's1' }

const NO_MUSIC: MusicTags = { kind: 'none' }
const TAGGED = addPlaylist(NO_TAGS, { id: 'p1', name: 'Hills' })

const OFF: SwedebeatMixAvailability = { kind: 'off' }
const MIXES: SwedebeatMixAvailability = { kind: 'ready' }

const connect = { need: 'connect', blocking: true } as const
const consent = { need: 'consent', blocking: true } as const

describe('the composer Spotify gate', () => {
  const cases: [string, SongsAvailability, SwedebeatMixAvailability, MusicTags, unknown][] = []
  const songs: [string, SongsAvailability, unknown][] = [
    ['no Spotify', NEEDS, connect],
    ['needs consent', CONSENT, consent],
    ['ready', READY, null],
  ]
  const choices: [string, MusicTags, boolean][] = [
    ['no tags', NO_TAGS, true],
    ['tags', TAGGED, true],
    ['"No music"', NO_MUSIC, false],
  ]
  // SwedeBeat mixes on or off never changes the answer: the composer cannot attach a mix.
  for (const [swedebeatName, swedebeat] of [['SwedeBeat off', OFF], ['SwedeBeat ready', MIXES]] as const) {
    for (const [songsName, availability, blocked] of songs) {
      for (const [choiceName, state, gated] of choices) {
        cases.push([`${songsName}, ${choiceName}, ${swedebeatName}`, availability, swedebeat, state, gated ? blocked : null])
      }
    }
  }
  it.each(cases)('%s', (_name, availability, swedebeat, state, expected) => {
    expect(spotifyGate(availability, swedebeat, gateOverride(state), false)).toEqual(expected)
  })

  it('does not gate a running class, which has playlists only', () => {
    expect(spotifyGate(CONSENT, OFF, gateOverride(TAGGED), true)).toBeNull()
  })
})
