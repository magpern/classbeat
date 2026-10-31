import { CLASS_ID, JOB_ID } from './jobFixtures'

export const LIST_ID = '55555555-5555-4555-8555-555555555555'
export const HASH = 'ab'.repeat(32)
export const MOWL_ID = 'c0000000-0000-4000-8000-000000000001'
export const SPOTIFY_ID = 'c0000000-0000-4000-8000-000000000002'
export const CLASS_PATH = `/api/classes/${CLASS_ID}`
export const LIST_PATH = `${CLASS_PATH}/revisions/${LIST_ID}`
export const NEW_JOB = {
  id: '66666666-6666-4666-8666-666666666666',
  kind: 'design_with_songs',
  state: 'queued',
  stage: null,
  class_id: CLASS_ID,
  cancel_requested: false,
  approval_revision_id: null,
  expires_at: null,
  approved_at: null,
  error_category: null,
  created_at: '2026-10-30T10:00:00Z',
  updated_at: '2026-10-30T10:00:00Z',
  finished_at: null,
}

/** A `GET .../revisions/{id}` answer for a song_list (no Spotify ids, no covers). */
export function listBody(extra: Record<string, unknown> = {}) {
  return {
    id: LIST_ID,
    number: 2,
    kind: 'song_list',
    created_at: '2026-10-30T09:00:00Z',
    parent_id: null,
    parent_number: null,
    status: 'waiting',
    job_id: JOB_ID,
    expires_at: '2026-11-06T09:00:00Z',
    approved_at: null,
    content_hash: HASH,
    mode: 'pick',
    target_sec: 300,
    total_sec: 360,
    crossfade_sec: 9,
    songs: [
      { position: 1, title: 'Hills <b>bold</b>', artist: 'Ann', role: 'climb', bpm: 92, length_sec: 241, new: false },
      { position: 2, title: 'Rolling <i>hills</i>', artist: 'Bo', role: 'warm_up', bpm: 120.4, length_sec: 119, new: true },
    ],
    omitted_no_bpm: 1,
    note: 'A mix <script>x</script>',
    ...extra,
  }
}

export function waitingJob(extra: Record<string, unknown> = {}) {
  return {
    ...NEW_JOB,
    id: JOB_ID,
    state: 'waiting_for_approval',
    approval_revision_id: LIST_ID,
    expires_at: '2026-11-06T09:00:00Z',
    ...extra,
  }
}

export function connectionsBody(spotify = true) {
  const base = { status: 'connected', last_error: null, connected_at: '2026-10-28T10:00:00Z', last_used_at: null }
  return {
    connections: [
      { id: MOWL_ID, provider: 'mowl', ...base },
      ...(spotify ? [{ id: SPOTIFY_ID, provider: 'spotify', ...base }] : []),
    ],
  }
}

export const BRIEF = {
  length_min: 45,
  intensity: 'hard',
  climbs: 2,
  sprints: 0,
  races: 0,
  ftp_test: null,
  style: 'hills',
  text: 'Hills',
}
