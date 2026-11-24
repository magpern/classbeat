import { useCallback, useEffect, useState } from 'react'
import { getConnectionsView } from '../../api/connections'
import { getMowlPlaylists } from '../../api/jobs'
import { getSpotifyStatus, type SpotifyStatus } from '../../api/spotify'
import { songsAvailability } from '../../components/newclass/source'
import type { PlaylistTag } from '../../components/start/musicTags'
import type { SongsAvailability } from '../../components/songs/MusicChoice'

export interface MusicSetup {
  /** What Spotify allows, or null until it is read. */
  songs: SongsAvailability | null
  /** The Spotify status for the consent switch; read only while the consent is missing. */
  status: SpotifyStatus | null
  /** The effective `soundless_enabled` flag: "Continue without music" is offered only with it. */
  soundlessEnabled: boolean
  mowlConnectionId: string | null
  /** The instructor's Spotify playlists the composer's picker offers. */
  offered: readonly PlaylistTag[]
  /** Reads Spotify again (after the consent was given). */
  refresh: () => void
}

/**
 * What the music question and the consent card need beyond the class (ADR 0038 amendment of 2026-11-24):
 * Spotify's state, the consent status, the playlists on offer and the `soundless_enabled` flag. It reads
 * only while `needed` (a composer class with a decision whose song search has not started), so every
 * other class makes no extra call. A failed read leaves `songs` as "unreachable": the search waits.
 */
export function useMusicSetup(needed: boolean): MusicSetup {
  const [setup, setSetup] = useState<Omit<MusicSetup, 'refresh'>>({
    songs: null,
    status: null,
    soundlessEnabled: false,
    mowlConnectionId: null,
    offered: [],
  })
  const [version, setVersion] = useState(0)
  useEffect(() => {
    if (!needed) return
    const abort = new AbortController()
    void (async () => {
      try {
        const view = await getConnectionsView()
        const songs = await songsAvailability(view.connections, abort.signal)
        const status = songs.kind === 'needs_consent' ? await getSpotifyStatus(abort.signal) : null
        const mowl = view.connections.find((c) => c.provider === 'mowl' && c.status === 'connected')
        const offered = await getMowlPlaylists(abort.signal).then(
          (found) => found.map((p) => ({ id: p.spotifyPlaylistId, name: p.name })),
          () => [],
        )
        if (abort.signal.aborted) return
        setSetup({ songs, status, soundlessEnabled: view.soundlessEnabled, mowlConnectionId: mowl?.id ?? null, offered })
      } catch {
        if (!abort.signal.aborted) setSetup((was) => ({ ...was, songs: { kind: 'off', unreachable: true } }))
      }
    })()
    return () => {
      abort.abort()
    }
  }, [needed, version])
  const refresh = useCallback(() => {
    setVersion((v) => v + 1)
  }, [])
  return { ...setup, refresh }
}
