import type { MusicOverride } from '../../api/interpret'
import type { Playlist } from '../../api/jobs'
import type { SwedebeatMix } from '../../api/swedebeatMixes'
import type { TFunction } from '../../i18n/t'
import type { SongsAvailability, SwedebeatMixAvailability } from '../songs/MusicChoice'

// The rows of the start screen's music picker (pure). Every row says where the music comes from;
// none says anything about the music itself (no tempo, no energy: we have no such data for a name).

export interface MusicRow {
  /** Stable id of the row: `playlist:<id>`, `mix:<id>`, `pick` or `none`. */
  key: string
  title: string
  /** The source label shown under the title, for example "Spotify playlist · in MOWL". */
  source: string
  override: MusicOverride
}

export interface MusicOffer {
  playlists: Playlist[]
  mixes: SwedebeatMix[]
  songs: SongsAvailability
  swedebeat: SwedebeatMixAvailability
  soundlessEnabled: boolean
  running: boolean
}

/** What the instructor can pick; a running class has playlists only (as on the advanced form). */
export function musicRows(t: TFunction, offer: MusicOffer): MusicRow[] {
  const rows: MusicRow[] = offer.playlists.map((p) => ({
    key: `playlist:${p.spotifyPlaylistId}`,
    title: p.name,
    source: t('start.music.src.playlist'),
    override: { source: 'mowl_spotify_playlist', spotify_playlist_id: p.spotifyPlaylistId },
  }))
  if (offer.running) return rows
  if (offer.swedebeat.kind === 'ready') {
    for (const mix of offer.mixes) {
      rows.push({
        key: `mix:${mix.mixId}`,
        title: mix.name,
        source: t('start.music.src.mix'),
        override: { source: 'mowl_swedebeat_mix', mix_id: mix.mixId },
      })
    }
  }
  if (offer.songs.kind === 'ready') {
    rows.push({ key: 'pick', title: t('start.music.pick'), source: t('music.pickHint'), override: { source: 'pick' } })
  }
  if (offer.soundlessEnabled) {
    rows.push({ key: 'none', title: t('music.none'), source: t('music.noneHint'), override: { source: 'none' } })
  }
  return rows
}

/** The rows whose title or source contains `query` (case-insensitive); all of them for an empty query. */
export function searchRows(rows: MusicRow[], query: string): MusicRow[] {
  const q = query.trim().toLocaleLowerCase()
  if (q === '') return rows
  return rows.filter((r) => `${r.title} ${r.source}`.toLocaleLowerCase().includes(q))
}
