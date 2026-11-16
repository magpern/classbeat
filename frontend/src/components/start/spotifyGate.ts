import type { MusicOverride } from '../../api/interpret'
import type { SongsAvailability, SwedebeatMixAvailability } from '../songs/MusicChoice'

// What a class with music needs from Spotify before the request is sent (roadmap Amendment 25;
// pure). Songs are found on Spotify and the class's playlist is made there, so every cycling class
// but "No music" and a Swedebeat mix needs a Spotify connection. The server enforces the same
// rule (`needs_spotify`); this only tells the instructor first.

export interface SpotifyGate {
  /** `connect`: no Spotify account is connected; `consent`: connected, but the working playlist is not allowed. */
  need: 'connect' | 'consent'
  /** Whether the request cannot go on (false: the request may still name a Swedebeat mix). */
  blocking: boolean
}

/** The Spotify connection to name in the request: connected, whether or not the working playlist is allowed. */
export function spotifyConnectionId(songs: SongsAvailability): string | undefined {
  return songs.kind === 'ready' || songs.kind === 'needs_consent' ? songs.spotifyConnectionId : undefined
}

/**
 * What stops the request, or null. `override` is the chosen music row (null: classbeat chooses from the
 * request). A running class has MOWL playlists only (ADR 0018). `off` (Spotify is not offered on this
 * server) has nothing to connect, so the server answers.
 */
export function spotifyGate(
  songs: SongsAvailability,
  swedebeat: SwedebeatMixAvailability,
  override: MusicOverride | null,
  running: boolean,
): SpotifyGate | null {
  if (running || songs.kind === 'ready' || songs.kind === 'off') return null
  if (override !== null && (override.source === 'none' || override.source === 'mowl_swedebeat_mix')) return null
  if (songs.kind === 'needs_consent') {
    // A playlist the instructor chose needs the connection only; a pick (or a request left to classbeat) needs the consent.
    return override === null || override.source === 'pick' ? { need: 'consent', blocking: true } : null
  }
  return { need: 'connect', blocking: override !== null || swedebeat.kind !== 'ready' }
}
