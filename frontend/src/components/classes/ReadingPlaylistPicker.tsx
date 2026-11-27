import { useEffect, useState } from 'react'
import { getMowlPlaylists } from '../../api/jobs'
import { useT } from '../../i18n/I18n'
import { MusicTagRow } from '../start/MusicTagRow'
import { togglePlaylist, type MusicTags, type PlaylistTag } from '../start/musicTags'

/**
 * The playlist picker of the confirm screen (#1538), shown under the closed "Use one of my
 * playlists" answer: the instructor's Spotify playlists as MOWL lists them (the list of the start
 * screen's "Add music", read when the picker first shows), at most five, each once. The ids are sent
 * with the re-read; the server checks them again. No mix is offered (ADR 0038 section 3).
 */
export function ReadingPlaylistPicker({ tags, onChange }: { tags: MusicTags; onChange: (tags: MusicTags) => void }) {
  const t = useT()
  const [offered, setOffered] = useState<readonly PlaylistTag[] | null>(null)
  useEffect(() => {
    const abort = new AbortController()
    getMowlPlaylists(abort.signal).then(
      (found) => {
        if (!abort.signal.aborted) setOffered(found.map((p) => ({ id: p.spotifyPlaylistId, name: p.name })))
      },
      () => {
        if (!abort.signal.aborted) setOffered([])
      },
    )
    return () => {
      abort.abort()
    }
  }, [])
  if (offered === null) {
    return (
      <p className="m-0 text-muted" role="status" aria-busy="true">
        {t('common.loading')}
      </p>
    )
  }
  return (
    <div className="flex flex-col gap-2" data-testid="reading-playlists">
      <p className="m-0 text-[0.875rem] text-muted">{t('cw.music.playlists.hint')}</p>
      <MusicTagRow
        offered={offered}
        tags={tags}
        noMusicOffered={false}
        onTogglePlaylist={(tag) => {
          onChange(togglePlaylist(tags, tag))
        }}
        onToggleNone={() => undefined}
      />
    </div>
  )
}
