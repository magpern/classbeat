import { request } from './client'

// The answer to the composer's music question (ADR 0038 amendment of 2026-11-24, #1495):
// `PUT /api/classes/{id}/music`. It stores the answer on the class; it makes no Spotify, MOWL or model
// call, starts no job and costs no credit. 409 `music_fixed` once the song search started for the
// sketch, 409 `feature_off` with the composer switch off.

export type MusicAnswer =
  | { choice: 'pick' }
  | { choice: 'later' }
  | { choice: 'describe'; note: string }
  | { choice: 'playlists'; playlistIds: readonly string[] }

/** The request body: playlists go as `spotify_playlist` sources, never more than the server's five. */
export function musicAnswerBody(answer: MusicAnswer): Record<string, unknown> {
  if (answer.choice === 'describe') return { choice: 'describe', note: answer.note.trim() }
  if (answer.choice === 'playlists') {
    return { choice: 'playlists', playlists: answer.playlistIds.map((id) => ({ kind: 'spotify_playlist', id })) }
  }
  return { choice: answer.choice }
}

export async function putMusicAnswer(classId: string, answer: MusicAnswer): Promise<void> {
  await request('PUT', `/api/classes/${encodeURIComponent(classId)}/music`, { body: musicAnswerBody(answer) })
}
