import type { SongsAvailability, SwedebeatMixAvailability } from '../songs/MusicChoice'
import { attachedPlaylists, gateOverride, type MusicTags } from './musicTags'
import { spotifyGate, type SpotifyGate } from './spotifyGate'

// What stops a composer request for want of Spotify (ADR 0038 amendment of 2026-11-24, decision 5; pure).
// The composer asks about the music after the sketch, and Spotify is needed only by the song search, so a
// request with the music left open is not blocked for a missing connection or consent: the page says songs
// need Spotify instead. Attached playlists still need the connection (the server refuses them without it), and
// the older start screen keeps `spotifyGate` as it was.

export interface ComposerGate {
  gate: SpotifyGate | null
  /** The request goes on, and Spotify is not connected yet: say "connect it after the sketch". */
  songsLater: boolean
}

export function composerSpotifyGate(
  songs: SongsAvailability,
  swedebeat: SwedebeatMixAvailability,
  tags: MusicTags,
  running: boolean,
): ComposerGate {
  const gate = spotifyGate(songs, swedebeat, gateOverride(tags), running)
  const open = !running && tags.kind === 'playlists' && attachedPlaylists(tags).length === 0
  if (gate === null || !open || gate.need === 'unavailable') return { gate, songsLater: false }
  return { gate: null, songsLater: gate.need === 'connect' }
}
