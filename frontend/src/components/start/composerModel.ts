import type { InterpretInput } from '../../api/interpret'
import { initialToggle, sportOverride, type SportToggle } from '../../lib/sportHint'
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
  /** The instructor chose the sport by hand (`lib/sportHint`): then it is sent whatever it is. */
  sportByHand?: boolean
}

/**
 * What `POST /api/jobs` takes for `interpret_request`: the music as `music_sources` or `music_override`, never
 * both; the sport as `sport_override` when it was chosen by hand or is Running (`sportOverride`, #1544).
 */
export function composerInput(request: ComposerRequest): InterpretInput {
  const sport = sportOverride({ sport: request.sport, manual: request.sportByHand === true })
  return {
    request: request.words,
    mowl_connection_id: request.mowlConnectionId,
    ...musicInput(request.tags),
    ...(sport === undefined ? {} : { sport_override: sport }),
    ...(request.spotifyConnectionId === undefined ? {} : { spotify_connection_id: request.spotifyConnectionId }),
  }
}

export interface ComposerDraft {
  request: string
  tags: MusicTags
  sport: SportToggle
}

/**
 * The first state: a kept request ("Try again", `?from=`) wins over an example's words (`?example=`). Kept music
 * comes back as the playlists still on offer, or "No music" where that is offered (cycling only). The sport
 * follows the words, or the kept request's sport (by hand when the words say otherwise): running is always
 * offered (#1544).
 */
export function initialDraft(options: {
  kept: KeptRequest | null
  exampleWords: string | null
  offered: readonly PlaylistTag[]
  noMusicOffered: boolean
}): ComposerDraft {
  const { kept, exampleWords, offered, noMusicOffered } = options
  if (kept === null) {
    const request = exampleWords ?? ''
    return { request, tags: NO_TAGS, sport: initialToggle(request) }
  }
  const toggle = initialToggle(kept.request, kept.sport)
  const sport: Sport = toggle.sport
  const ids = kept.playlists ?? (kept.music?.startsWith('playlist:') === true ? [kept.music.slice('playlist:'.length)] : [])
  const tags: MusicTags =
    kept.music === 'none' && noMusicOffered && sport === 'cycling' ? { kind: 'none' } : tagsFromKept(ids, offered)
  return { request: kept.request, tags, sport: toggle }
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
