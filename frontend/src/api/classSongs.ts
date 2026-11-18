import { object, optionalText, text } from './decode'

// The class summary's song list pointer and playlist (split out of classes.ts, #1191 rule 2).

/** The class's newest song list (ADR 0008 section 9). */
export interface ClassSongs {
  /** `none`, `waiting`, `approved`, `expired`, `superseded` or `cancelled`. */
  state: string
  jobId: string | null
  revisionId: string | null
  contentHash: string | null
  expiresAt: string | null
}

export interface ClassPlaylist {
  name: string
  /** The owner's own link, only when the server built it from a verified playlist. */
  link: string | null
}

const SPOTIFY_PLAYLIST_LINK = /^https:\/\/open\.spotify\.com\/playlist\/[A-Za-z0-9]{22}$/

const SPOTIFY_TRACK_LINK = /^https:\/\/open\.spotify\.com\/track\/[A-Za-z0-9]{22}$/

/** Whether a link from the API is a Spotify track link; the frontend never builds one. */
export function isTrackLink(link: string): boolean {
  return SPOTIFY_TRACK_LINK.test(link)
}

/** Whether a link from the API is a Spotify playlist link; the frontend never builds one. */
export function isPlaylistLink(link: string): boolean {
  return SPOTIFY_PLAYLIST_LINK.test(link)
}

export function decodePlaylist(value: unknown): ClassPlaylist | null {
  if (value === null || value === undefined) return null
  const item = object(value, 'playlist')
  const link = optionalText(item, 'link')
  return {
    name: text(item, 'name'),
    link: link !== null && SPOTIFY_PLAYLIST_LINK.test(link) ? link : null,
  }
}

export function decodeSongs(value: unknown): ClassSongs {
  if (value === null || value === undefined) {
    return { state: 'none', jobId: null, revisionId: null, contentHash: null, expiresAt: null }
  }
  const item = object(value, 'songs')
  return {
    state: text(item, 'state'),
    jobId: optionalText(item, 'job_id'),
    revisionId: optionalText(item, 'revision_id'),
    contentHash: optionalText(item, 'content_hash'),
    expiresAt: optionalText(item, 'expires_at'),
  }
}

