import { get, post } from './client'
import { count, flag, list, object, optionalCount, optionalText, text } from './decode'
import { isTrackLink } from './classes'
import { decodeInterpretationItems, type InterpretationItem } from './interpretationItems'
import { decodeJob, type Brief, type Job } from './jobs'
import { decodeDraftContent, type DraftContent } from './draftTypes'

// Song picking (ADR 0008 sections 4 to 6 and 9): the song list a job waits on, the approval, and
// the jobs that pick, change or resume. Server text is data: it is decoded into typed fields and
// rendered as text. A song has no Spotify id here, and the page never shows an id.

export const PICK_KIND = 'design_with_songs'
export const REVISE_KIND = 'revise_songs'

export function isSongKind(kind: string): boolean {
  return kind === PICK_KIND || kind === REVISE_KIND
}

export type ListStatus = 'waiting' | 'approved' | 'expired' | 'superseded' | 'cancelled'

const STATUSES: readonly string[] = ['waiting', 'approved', 'expired', 'superseded', 'cancelled']

/**
 * Where a song came from (ADR 0038 section 10.2), as `source: {kind, playlist?}` on each song of a
 * composer list (#1188): `pinned` (named by the instructor), `playlist` (an attached one: `index` is
 * its position in the request's attached playlists, which the server stores without names) or
 * `search` (found by classbeat). Absent on older lists.
 */
export type SongSource = { kind: 'pinned' } | { kind: 'playlist'; index: number } | { kind: 'search' }

export interface Song {
  position: number
  title: string
  artist: string
  role: string
  bpm: number
  lengthSec: number
  /** When it starts in the class: the lengths before it less the crossfade at each change of song. */
  startSec: number
  isNew: boolean
  /** The album cover URL, shown only through `CoverArt` (ADR 0013 section 9), or null. */
  cover: string | null
  /** The server-built link back to Spotify (ADR 0008 section 9), only when it is a track link. */
  openSpotify: string | null
  /** Composer lists only; absent or null for an older list. */
  source?: SongSource | null
}

export interface SongList {
  id: string
  number: number
  status: ListStatus
  jobId: string | null
  expiresAt: string | null
  approvedAt: string | null
  /** What Approve sends back with the revision id. */
  contentHash: string
  mode: string
  targetSec: number
  totalSec: number
  songs: Song[]
  omittedNoBpm: number
  note: string
  /** What the list could not do as asked (issue #1133): Not as asked items, worded by code. */
  unmet: InterpretationItem[]
  /**
   * A `v: 3` list's designed class (ADR 0038 section 5, `preview.content`): what the approval covers
   * with the songs. Absent or null for a `v: 1` or `v: 2` list.
   */
  preview?: DraftContent | null
}

/** A link from the API is kept only when it is a Spotify track link. */
function trackLink(link: string | null): string | null {
  return link !== null && isTrackLink(link) ? link : null
}

function decodeSource(value: unknown): SongSource | null {
  if (typeof value !== 'object' || value === null) return null
  const item = value as Record<string, unknown>
  if (item.kind === 'pinned') return { kind: 'pinned' }
  if (item.kind === 'search') return { kind: 'search' }
  if (item.kind === 'playlist' && typeof item.playlist === 'number' && Number.isInteger(item.playlist) && item.playlist >= 0) {
    return { kind: 'playlist', index: item.playlist }
  }
  return null
}

interface Preview {
  content: DraftContent
  /** Each song's heard start and end in the designed class, in play order. */
  spans: { start: number; end: number }[]
}

function decodePreview(value: unknown): Preview | null {
  if (typeof value !== 'object' || value === null) return null
  const item = value as Record<string, unknown>
  try {
    const spans = list(item, 'songs').map((span) => {
      const one = object(span, 'span')
      return { start: count(one, 'start'), end: count(one, 'end') }
    })
    return { content: decodeDraftContent(item.content), spans }
  } catch {
    return null
  }
}

function decodeSong(value: unknown): Omit<Song, 'startSec'> {
  const item = object(value, 'song')
  return {
    position: count(item, 'position'),
    title: text(item, 'title'),
    artist: text(item, 'artist'),
    role: text(item, 'role'),
    bpm: count(item, 'bpm'),
    lengthSec: count(item, 'length_sec'),
    isNew: flag(item, 'new'),
    // Older lists (v: 1) have no cover; CoverArt checks the host again before it shows one.
    cover: optionalText(item, 'cover'),
    openSpotify: trackLink(optionalText(item, 'open_spotify')),
    source: decodeSource(item.source),
  }
}

/**
 * Start times from what the API sends: the picker's own rule (total time = lengths less the
 * crossfade at every change of song), so the last song ends at `totalSec`. The API carries no
 * per-song start of the analysed music.
 */
export function withStartTimes(songs: Omit<Song, 'startSec'>[], crossfadeSec: number): Song[] {
  const overlap = Number.isFinite(crossfadeSec) ? Math.max(crossfadeSec, 0) : 0
  let start = 0
  return songs.map((song) => {
    const placed = { ...song, startSec: start }
    start += Math.max(song.lengthSec - overlap, 0)
    return placed
  })
}

/**
 * A composer list's songs start where its preview heard them (the design placed them); any other
 * list, or a preview that does not match, falls back to the picker's rule.
 */
function placedSongs(songs: Omit<Song, 'startSec'>[], crossfadeSec: number, preview: Preview | null): Song[] {
  const spans = preview?.spans
  if (spans === undefined || spans.length !== songs.length) return withStartTimes(songs, crossfadeSec)
  return songs.map((song, i) => ({ ...song, startSec: spans[i]!.start }))
}

export function decodeSongList(value: unknown): SongList {
  const item = object(value, 'song list')
  const status = text(item, 'status')
  const preview = decodePreview(item.preview)
  const crossfade = optionalCount(item, 'crossfade_sec') ?? 0
  const songs = list(item, 'songs').map(decodeSong)
  return {
    id: text(item, 'id'),
    number: count(item, 'number'),
    status: STATUSES.includes(status) ? (status as ListStatus) : 'cancelled',
    jobId: optionalText(item, 'job_id'),
    expiresAt: optionalText(item, 'expires_at'),
    approvedAt: optionalText(item, 'approved_at'),
    contentHash: text(item, 'content_hash'),
    mode: text(item, 'mode'),
    targetSec: count(item, 'target_sec'),
    totalSec: count(item, 'total_sec'),
    songs: placedSongs(songs, crossfade, preview),
    omittedNoBpm: optionalCount(item, 'omitted_no_bpm') ?? 0,
    note: optionalText(item, 'note') ?? '',
    unmet: (decodeInterpretationItems(item.unmet) ?? []).filter((i) => i.origin === 'not_as_asked'),
    preview: preview?.content ?? null,
  }
}

export async function getSongList(
  classId: string,
  revisionId: string,
  signal?: AbortSignal,
): Promise<SongList> {
  const path = `/api/classes/${encodeURIComponent(classId)}/revisions/${encodeURIComponent(revisionId)}`
  return decodeSongList(await get(path, signal))
}

/**
 * Approve songs: the revision id and the content hash of the list the page shows, nothing else
 * (ADR 0008 section 5). The server refuses a list that is superseded, expired or changed.
 */
export async function approveSongs(
  jobId: string,
  revisionId: string,
  contentHash: string,
): Promise<Job> {
  return decodeJob(
    await post(`/api/jobs/${encodeURIComponent(jobId)}/approve`, {
      revision_id: revisionId,
      content_hash: contentHash,
    }),
  )
}

/** What `POST /api/jobs` takes for `design_with_songs` (ADR 0008 section 4). */
export interface PickInput {
  title?: string
  class_id?: string
  brief: Brief
  mode: 'pick' | 'resume'
  /** Ask for changes: keep the other songs of this list and replace the slots named. */
  seed?: { song_list_revision_id: string; replace: number[] }
  /** Create the playlist again: the approved list to run phase 2 for. */
  resume?: { song_list_revision_id: string }
  /**
   * The user's own Spotify playlist (roadmap Amendment 26): its tracks in order are the list to
   * approve, and phase 2 makes a new private playlist. Only with mode `pick` and no `seed`.
   */
  source?: { source: 'mowl_spotify_playlist'; spotify_playlist_id: string }
  mowl_connection_id: string
  spotify_connection_id: string
}

export async function createPickJob(input: PickInput, idempotencyKey: string): Promise<Job> {
  const body = { kind: PICK_KIND, input }
  return decodeJob(await post('/api/jobs', body, { 'Idempotency-Key': idempotencyKey }))
}

/** What `POST /api/jobs` takes for `revise_songs` (ADR 0008 section 6, issue SJ3). */
interface ReviseSongsBase {
  class_id: string
  /** The class's current draft (the server refuses another with stale_base). */
  base_revision_id: string
  mowl_connection_id: string
  spotify_connection_id: string
}

export interface ReviseSongsPick extends ReviseSongsBase {
  mode: 'pick'
  /** Positions in the class's picked songs, or all of them. */
  replace: number[] | 'all'
  request: string
}

/** A resume names its list and nothing to change: `replace` or `request` is refused with a 422. */
export interface ReviseSongsResume extends ReviseSongsBase {
  mode: 'resume'
  resume: { song_list_revision_id: string }
}

export type ReviseSongsInput = ReviseSongsPick | ReviseSongsResume

export async function createReviseSongsJob(
  input: ReviseSongsInput,
  idempotencyKey: string,
): Promise<Job> {
  const body = { kind: REVISE_KIND, input }
  return decodeJob(await post('/api/jobs', body, { 'Idempotency-Key': idempotencyKey }))
}
