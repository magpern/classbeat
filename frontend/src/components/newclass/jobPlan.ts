import type { MusicOverride } from '../../api/interpret'
import type { Brief, DesignInput, RunningBrief } from '../../api/jobs'
import type { PickInput } from '../../api/songs'
import type { Music, SongsAvailability, SwedebeatMixAvailability } from '../songs/MusicChoice'
import { spotifyConnectionId, spotifyGate, type SpotifyGate } from '../start/spotifyGate'

// Which job the Advanced form starts (pure: no network, no React). The same rules as the start
// screen's hand-off (roadmap Amendments 25 and 26, ADR 0008 amendment of 2026-11-16):
// - "Pick for me" is a `design_with_songs` pick;
// - a cycling class on the instructor's own Spotify playlist, with a Spotify connection, is a
//   `design_with_songs` with that playlist as its `source`: the instructor approves the songs and
//   classbeat makes a new private playlist in class order. While song picking does not admit the
//   user (the server refuses `song_picking_off`, the kill switch of classbeat's Spotify writes)
//   the form sends the write-free `design_draft` on that playlist instead (`fallback`);
// - a Swedebeat mix, "No music", a running class, and a playlist while Spotify is not offered on
//   this server (`songs.kind === 'off'`) stay the `design_draft` they were.

export interface PlanValues {
  title: string
  brief: Brief | RunningBrief
  music: Music
  running: boolean
  noMusic: boolean
  playlist: string
  mixId: string
  songs: SongsAvailability
  mowlConnectionId: string
}

export type JobPlan =
  | { kind: 'pick'; pick: PickInput }
  | { kind: 'source'; pick: PickInput; fallback: DesignInput }
  | { kind: 'draft'; draft: DesignInput }

/** The form's music as the start screen's override row (null for a running class: MOWL playlists only). */
export function formOverride(v: Pick<PlanValues, 'music' | 'running' | 'noMusic' | 'playlist' | 'mixId'>): MusicOverride | null {
  if (v.running) return null
  if (v.noMusic) return { source: 'none' }
  if (v.music === 'swedebeat_mix') return { source: 'mowl_swedebeat_mix', mix_id: v.mixId }
  if (v.music === 'pick') return { source: 'pick' }
  return { source: 'mowl_spotify_playlist', spotify_playlist_id: v.playlist }
}

/**
 * What stops the form for want of Spotify, or null: the start screen's rule for the chosen music, except
 * that song picking not being offered (`off`) never stops the form: there is nothing to connect, and the
 * form starts the write-free `design_draft` on the chosen playlist (the start screen's own "unavailable"
 * line is for a request classbeat has to turn into songs; this form names the music itself).
 */
export function formGate(
  v: Pick<PlanValues, 'music' | 'running' | 'noMusic' | 'playlist' | 'mixId' | 'songs'>,
  swedebeat: SwedebeatMixAvailability,
): SpotifyGate | null {
  if (v.songs.kind === 'off') return null
  return spotifyGate(v.songs, swedebeat, formOverride(v), v.running)
}

function isCycling(brief: Brief | RunningBrief): brief is Brief {
  return brief.sport !== 'running'
}

export function planJob(v: PlanValues): JobPlan {
  const override = formOverride(v)
  const spotify = spotifyConnectionId(v.songs)
  const cycling = isCycling(v.brief) ? v.brief : null
  if (cycling !== null && override?.source === 'pick' && v.songs.kind === 'ready') {
    return { kind: 'pick', pick: pickInput(v, cycling, v.songs.spotifyConnectionId) }
  }
  const music: DesignInput['music'] =
    override === null || override.source === 'pick'
      ? { source: 'mowl_spotify_playlist', spotify_playlist_id: v.playlist }
      : override
  const draft: DesignInput = { title: v.title, brief: v.brief, music, mowl_connection_id: v.mowlConnectionId }
  if (cycling !== null && override?.source === 'mowl_spotify_playlist' && spotify !== undefined) {
    const pick: PickInput = { ...pickInput(v, cycling, spotify), source: override }
    return { kind: 'source', pick, fallback: draft }
  }
  return { kind: 'draft', draft }
}

function pickInput(v: PlanValues, brief: Brief, spotify: string): PickInput {
  return {
    title: v.title,
    brief,
    mode: 'pick',
    mowl_connection_id: v.mowlConnectionId,
    spotify_connection_id: spotify,
  }
}
