import type { MusicOverride, MusicSource } from '../../api/interpret'

// The attached-music tags of the composer (ADR 0038 section 10.1; pure). A class has no tags (classbeat
// chooses from the request), up to MAX_MUSIC_TAGS Spotify playlists (favoured, never a limit), or "No music".
// "No music" and the playlists exclude each other, in both directions.

/** The most Spotify playlists one request attaches (backend `music_sources`, ADR 0038 section 3). */
export const MAX_MUSIC_TAGS = 5

export interface PlaylistTag {
  /** The Spotify playlist id. */
  id: string
  name: string
}

export type MusicTags = { kind: 'none' } | { kind: 'playlists'; tags: readonly PlaylistTag[] }

export const NO_TAGS: MusicTags = { kind: 'playlists', tags: [] }

export const attachedPlaylists = (state: MusicTags): readonly PlaylistTag[] => (state.kind === 'none' ? [] : state.tags)

export const isAttached = (state: MusicTags, id: string): boolean => attachedPlaylists(state).some((tag) => tag.id === id)

/** Whether one more playlist fits; an attached one never needs room (choosing it again removes it). */
export const canAttach = (state: MusicTags): boolean => attachedPlaylists(state).length < MAX_MUSIC_TAGS

/** Attaches a playlist: "No music" is dropped, a repeat or a sixth playlist changes nothing. */
export function addPlaylist(state: MusicTags, tag: PlaylistTag): MusicTags {
  if (state.kind === 'none') return { kind: 'playlists', tags: [tag] }
  if (isAttached(state, tag.id) || !canAttach(state)) return state
  return { kind: 'playlists', tags: [...state.tags, tag] }
}

export function removePlaylist(state: MusicTags, id: string): MusicTags {
  return state.kind === 'none' ? state : { kind: 'playlists', tags: state.tags.filter((tag) => tag.id !== id) }
}

/** The menu's choice of a playlist: attached ones are removed, others added (see `addPlaylist`). */
export function togglePlaylist(state: MusicTags, tag: PlaylistTag): MusicTags {
  return isAttached(state, tag.id) ? removePlaylist(state, tag.id) : addPlaylist(state, tag)
}

/** "No music" clears every playlist; choosing it again, or removing its tag, clears it. */
export function toggleNone(state: MusicTags): MusicTags {
  return state.kind === 'none' ? NO_TAGS : { kind: 'none' }
}

/** What the request sends: `music_sources` or `music_override`, never both, nothing without tags. */
export function musicInput(state: MusicTags): { music_sources?: MusicSource[]; music_override?: MusicOverride } {
  if (state.kind === 'none') return { music_override: { source: 'none' } }
  if (state.tags.length === 0) return {}
  return { music_sources: state.tags.map((tag) => ({ kind: 'spotify_playlist' as const, id: tag.id })) }
}

/**
 * The override the Spotify gate reads (`spotifyGate.ts`). In the composer "favoured, not limited" means classbeat
 * may search beyond the attached playlists, so every class but "No music" is a song pick: it needs the Spotify
 * connection and the working-playlist consent, with or without attached playlists, empty tags included (the gate's
 * carve-out for `null` is for a SwedeBeat mix, which the composer cannot attach; Architect, #1190). Only "No music"
 * is exempt. The old start screen's own rule is unchanged.
 */
export function gateOverride(state: MusicTags): MusicOverride {
  return state.kind === 'none' ? { source: 'none' } : { source: 'pick' }
}

/** The tags again from kept ids (a "Try again" hand-off): only playlists still on offer, at most the cap. */
export function tagsFromKept(kept: readonly string[], offered: readonly PlaylistTag[]): MusicTags {
  const tags = kept.flatMap((id) => offered.filter((p) => p.id === id)).slice(0, MAX_MUSIC_TAGS)
  return { kind: 'playlists', tags }
}

/** A running class has playlists only (ADR 0018), so "No music" does not carry over to it. */
export function forSport(state: MusicTags, running: boolean): MusicTags {
  return running && state.kind === 'none' ? NO_TAGS : state
}

/** The playlists whose name contains `query` (case-insensitive); all of them for an empty query. */
export function searchPlaylists(offered: readonly PlaylistTag[], query: string): readonly PlaylistTag[] {
  const q = query.trim().toLocaleLowerCase()
  return q === '' ? offered : offered.filter((p) => p.name.toLocaleLowerCase().includes(q))
}
