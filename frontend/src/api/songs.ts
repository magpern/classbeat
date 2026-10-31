import { get, post } from './client'
import { count, flag, list, object, optionalCount, optionalText, text } from './decode'
import { decodeJob, type Brief, type Job } from './jobs'

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

export interface Song {
  position: number
  title: string
  artist: string
  role: string
  bpm: number
  lengthSec: number
  isNew: boolean
  /** The album cover URL, shown only through `CoverArt` (ADR 0013 section 9), or null. */
  cover: string | null
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
}

function decodeSong(value: unknown): Song {
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
  }
}

export function decodeSongList(value: unknown): SongList {
  const item = object(value, 'song list')
  const status = text(item, 'status')
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
    songs: list(item, 'songs').map(decodeSong),
    omittedNoBpm: optionalCount(item, 'omitted_no_bpm') ?? 0,
    note: optionalText(item, 'note') ?? '',
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
  mowl_connection_id: string
  spotify_connection_id: string
}

export async function createPickJob(input: PickInput, idempotencyKey: string): Promise<Job> {
  const body = { kind: PICK_KIND, input }
  return decodeJob(await post('/api/jobs', body, { 'Idempotency-Key': idempotencyKey }))
}

/** What `POST /api/jobs` takes for `revise_songs` (ADR 0008 section 6, issue SJ3). */
export interface ReviseSongsInput {
  class_id: string
  base_revision_id: string
  /** Positions in the class's picked songs, or all of them. */
  replace: number[] | 'all'
  request: string
  mode: 'pick' | 'resume'
  /** Create the playlist again: the approved list to run phase 2 for. */
  resume?: { song_list_revision_id: string }
  mowl_connection_id: string
  spotify_connection_id: string
}

export async function createReviseSongsJob(
  input: ReviseSongsInput,
  idempotencyKey: string,
): Promise<Job> {
  const body = { kind: REVISE_KIND, input }
  return decodeJob(await post('/api/jobs', body, { 'Idempotency-Key': idempotencyKey }))
}
