import type { InterpretInput } from '../../api/interpret'
import type { KeptRequest } from '../../lib/startRequest'
import { NO_TAGS, musicInput, tagsFromKept, type MusicTags, type PlaylistTag } from './musicTags'

// The composer's request and its first state (ADR 0038 section 10.1; pure: no React, no network).

export type Sport = 'cycling' | 'running'

export interface ComposerRequest {
  /** What the instructor wrote, trimmed; never empty. */
  words: string
  mowlConnectionId: string
  spotifyConnectionId: string | undefined
  tags: MusicTags
  sport: Sport
}

/** What `POST /api/jobs` takes for `interpret_request`: the music as `music_sources` or `music_override`, never both. */
export function composerInput(request: ComposerRequest): InterpretInput {
  return {
    request: request.words,
    mowl_connection_id: request.mowlConnectionId,
    ...musicInput(request.tags),
    ...(request.sport === 'running' ? { sport_override: 'running' as const } : {}),
    ...(request.spotifyConnectionId === undefined ? {} : { spotify_connection_id: request.spotifyConnectionId }),
  }
}

export interface ComposerDraft {
  request: string
  tags: MusicTags
  sport: Sport
}

/**
 * The first state: a kept request ("Try again", `?from=`) wins over an example's words (`?example=`). Kept music
 * comes back as the playlists still on offer, or "No music" where that is offered; running only where it is enabled.
 */
export function initialDraft(options: {
  kept: KeptRequest | null
  exampleWords: string | null
  offered: readonly PlaylistTag[]
  runningEnabled: boolean
  noMusicOffered: boolean
}): ComposerDraft {
  const { kept, exampleWords, offered, runningEnabled, noMusicOffered } = options
  if (kept === null) return { request: exampleWords ?? '', tags: NO_TAGS, sport: 'cycling' }
  const sport: Sport = runningEnabled && kept.sport === 'running' ? 'running' : 'cycling'
  const ids = kept.playlists ?? (kept.music?.startsWith('playlist:') === true ? [kept.music.slice('playlist:'.length)] : [])
  const tags: MusicTags =
    kept.music === 'none' && noMusicOffered && sport === 'cycling' ? { kind: 'none' } : tagsFromKept(ids, offered)
  return { request: kept.request, tags, sport }
}

/** The kept form of a started request, so "Try again" opens the composer as it was. */
export function keptFromRequest(request: ComposerRequest): KeptRequest {
  const tags = request.tags
  const ids = tags.kind === 'none' ? [] : tags.tags.map((tag) => tag.id)
  const first = ids[0]
  return {
    request: request.words,
    music: tags.kind === 'none' ? 'none' : first === undefined ? null : `playlist:${first}`,
    playlists: ids,
    sport: request.sport,
  }
}
