import { describe, expect, it } from 'vitest'
import type { MusicDecision } from '../../api/composerState'
import type { SongsAvailability } from '../../components/songs/MusicChoice'
import { musicGate } from './musicGate'

const UNDECIDED: MusicDecision = { state: 'undecided', choice: null, playlists: 0 }
const DECIDED: MusicDecision = { state: 'decided', choice: 'pick', playlists: 0 }
const READY: SongsAvailability = { kind: 'ready', spotifyConnectionId: 's1' }

describe('the gate of the song search', () => {
  const rows: [string, MusicDecision | null, SongsAvailability | null, boolean, string][] = [
    ['undecided wins over everything, even a connected Spotify', UNDECIDED, READY, true, 'undecided'],
    ['undecided with no Spotify is still undecided', UNDECIDED, { kind: 'needs_spotify' }, true, 'undecided'],
    ['decided, Spotify not read yet', DECIDED, null, true, 'checking'],
    ['decided, no Spotify', DECIDED, { kind: 'needs_spotify' }, true, 'not_connected'],
    ['decided, connected, no consent', DECIDED, { kind: 'needs_consent', spotifyConnectionId: 's1' }, true, 'consent_missing'],
    ['decided, song picking off', DECIDED, { kind: 'off' }, true, 'unavailable'],
    ['decided, status unreadable', DECIDED, { kind: 'off', unreachable: true }, true, 'unavailable'],
    ['decided, connected, consent given', DECIDED, READY, true, 'ready'],
    ['no decision (an older class) behaves as before', null, { kind: 'needs_spotify' }, true, 'ready'],
    ['the search has started: nothing is waited for', UNDECIDED, null, false, 'ready'],
  ]
  it.each(rows)('%s', (_name, decision, songs, watching, gate) => {
    expect(musicGate(decision, songs, watching)).toBe(gate)
  })
})
