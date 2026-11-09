import { ApiError } from '../../api/client'
import type { CatalogueKey } from '../../i18n/en'
import { getConnectionsView, type Connection } from '../../api/connections'
import { getSwedebeatMixes, swedebeatMixListError, type SwedebeatMix } from '../../api/swedebeatMixes'
import { getMowlPlaylists, type Playlist } from '../../api/jobs'
import { getSpotifyStatus } from '../../api/spotify'
import type { SongsAvailability, SwedebeatMixAvailability } from '../songs/MusicChoice'

/** What the New class page knows once it has asked MOWL, Spotify and Swedebeat what is on offer. */
export type Source =
  | { status: 'loading' }
  | { status: 'no_mowl'; reconnect: boolean }
  | { status: 'playlists_failed'; message: CatalogueKey }
  | {
      status: 'ready'
      connection: Connection
      playlists: Playlist[]
      songs: SongsAvailability
      swedebeat: SwedebeatMixAvailability
      mixes: SwedebeatMix[]
      runningEnabled: boolean
      soundlessEnabled: boolean
    }

const PLAYLIST_ERRORS: Record<string, CatalogueKey> = {
  upstream_unavailable: 'newclass.playlistError.unavailable',
  key_unavailable: 'newclass.playlistError.unavailable',
  rate_limited: 'newclass.playlistError.rateLimited',
}

function playlistError(error: unknown): CatalogueKey {
  if (error instanceof ApiError && error.reason !== null && error.status !== 409) {
    const known = Object.hasOwn(PLAYLIST_ERRORS, error.reason) ? PLAYLIST_ERRORS[error.reason] : undefined
    if (known !== undefined) return known
  }
  return 'newclass.playlistError.default'
}

/** Whether "Pick for me" is offered, and what it still needs (ADR 0008 section 9). */
async function songsAvailability(connections: Connection[], signal: AbortSignal): Promise<SongsAvailability> {
  let status
  try {
    status = await getSpotifyStatus(signal)
  } catch {
    return { kind: 'off' }
  }
  if (status === null) return { kind: 'off' }
  const spotify = connections.find((c) => c.provider === 'spotify')
  if (spotify === undefined || spotify.status !== 'connected' || status.status !== 'connected') {
    return { kind: 'needs_spotify' }
  }
  if (!status.poolConsent) return { kind: 'needs_consent' }
  return { kind: 'ready', spotifyConnectionId: spotify.id }
}

/** Asks for the connections, the playlists, song picking and the Swedebeat mixes; `null` once aborted. */
export async function loadSource(signal: AbortSignal): Promise<Source | null> {
  try {
    const { connections, swedebeat: swedebeatAccess, runningEnabled, soundlessEnabled } = await getConnectionsView()
    const mowl = connections.find((c) => c.provider === 'mowl')
    if (mowl === undefined || mowl.status !== 'connected') return { status: 'no_mowl', reconnect: mowl !== undefined }
    const playlists = await getMowlPlaylists(signal)
    const songs = await songsAvailability(connections, signal)
    let swedebeat: SwedebeatMixAvailability = { kind: 'off' }
    let mixes: SwedebeatMix[] = []
    // The flag and the user's own "I use Swedebeat" (#525): with either off, no mix choice and no listing.
    if (swedebeatAccess.enabled && swedebeatAccess.usesSwedebeat) {
      try {
        const listed = await getSwedebeatMixes(signal)
        if (listed !== null) {
          swedebeat = { kind: 'ready' }
          mixes = listed
        }
      } catch (error) {
        if (!signal.aborted) swedebeat = { kind: 'failed', message: swedebeatMixListError(error) }
      }
    }
    return { status: 'ready', connection: mowl, playlists, songs, swedebeat, mixes, runningEnabled, soundlessEnabled }
  } catch (error) {
    if (signal.aborted) return null
    if (error instanceof ApiError && error.status === 409) {
      return { status: 'no_mowl', reconnect: error.reason !== 'not_connected' }
    }
    return { status: 'playlists_failed', message: playlistError(error) }
  }
}
