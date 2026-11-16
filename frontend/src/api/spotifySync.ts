import { ApiError, get, post } from './client'
import { isObject, object, optionalText, text } from './decode'

// A studio revision's Spotify sync state and "Sync again" (ADR 0036 section 9; backend
// `classes/studio_sync_routes.py`). The API sends codes only: no playlist id and no track id.

export type SyncTarget = 'working_copy' | 'final'
export type SyncState = 'pending' | 'synced' | 'failed'

export interface SyncStatus {
  state: SyncState
  /** The failure's category (`superseded` when a newer list took the playlist over). */
  detail: string | null
}

export type SyncView = Record<SyncTarget, SyncStatus | null>

const STATES: readonly string[] = ['pending', 'synced', 'failed']

const base = (classId: string, revisionId: string) =>
  `/api/classes/${encodeURIComponent(classId)}/spotify-sync/${encodeURIComponent(revisionId)}`

function decodeStatus(value: unknown): SyncStatus | null {
  if (!isObject(value)) return null
  const state = text(value, 'state')
  if (!STATES.includes(state)) return null
  return { state: state as SyncState, detail: optionalText(value, 'detail') }
}

export function decodeSyncView(value: unknown): SyncView {
  const item = object(value, 'spotify sync')
  return { working_copy: decodeStatus(item.working_copy), final: decodeStatus(item.final) }
}

/** `null` for a class the server does not know or with the studio off (404). */
export async function getSpotifySync(classId: string, revisionId: string, signal?: AbortSignal): Promise<SyncView | null> {
  try {
    return decodeSyncView(await get(base(classId, revisionId), signal))
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return null
    throw error
  }
}

/**
 * "Sync again": the server starts one sync of a failed state and answers `not_failed` for anything
 * else, so a repeated press starts nothing.
 */
export async function syncAgain(classId: string, revisionId: string, target: SyncTarget): Promise<void> {
  await post(`${base(classId, revisionId)}/${target}`)
}
