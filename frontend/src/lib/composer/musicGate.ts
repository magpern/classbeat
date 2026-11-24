import type { MusicDecision } from '../../api/composerState'
import type { SongsAvailability } from '../../components/songs/MusicChoice'

// What the song search waits for (ADR 0038 amendment of 2026-11-24, decisions 5 to 8; pure). The server
// refuses `fill-songs` for the same reasons, in this order: `music_undecided`, `not_connected`,
// `consent_missing`. The page asks first, so it never starts a call it knows is refused.

export type MusicGate =
  | 'ready'
  | 'undecided'
  /** Spotify and the consent are still being read: nothing starts yet. */
  | 'checking'
  /** Song picking is not available, or its status could not be read. */
  | 'unavailable'
  | 'not_connected'
  | 'consent_missing'

/** `songs` is null until Spotify's state is read. */
export function songsGate(songs: SongsAvailability | null): Exclude<MusicGate, 'undecided'> {
  if (songs === null) return 'checking'
  switch (songs.kind) {
    case 'ready':
      return 'ready'
    case 'needs_spotify':
      return 'not_connected'
    case 'needs_consent':
      return 'consent_missing'
    case 'off':
      return 'unavailable'
  }
}

/**
 * The gate of a class. A class with no decision (an older class, or none the server asked about) is
 * decided: it behaves as before, and the server still enforces the rest. `watching` is false once the
 * song search started for the sketch (or the sketch is not current): nothing is waited for then.
 */
export function musicGate(decision: MusicDecision | null, songs: SongsAvailability | null, watching: boolean): MusicGate {
  if (decision === null || !watching) return 'ready'
  if (decision.state === 'undecided') return 'undecided'
  return songsGate(songs)
}
