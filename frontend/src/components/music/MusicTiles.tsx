import { Trans, useT } from '../../i18n/I18n'
import { Link } from '../../router/Router'
import type { Playlist } from '../../api/jobs'
import type { MadePlaylist } from '../../api/music'
import { CoverMosaic, initials } from '../songs/CoverArt'
import { Button } from '../ui/button'

const TILE = 'flex flex-col gap-3 rounded-lg border border-line bg-surface p-4 shadow-card'
const LIFT =
  'motion-safe:transition-[transform,box-shadow] motion-safe:duration-200 motion-safe:hover:-translate-y-0.5 motion-safe:hover:shadow-glow'

/** One playlist classbeat made: the cover art unaltered, the class it is for, OPEN SPOTIFY beside it. */
export function MadeCard({ playlist }: { playlist: MadePlaylist }) {
  const t = useT()
  return (
    <div className={`${TILE} ${LIFT} h-full`}>
      <CoverMosaic songs={playlist.songs} className="aspect-square h-auto w-full" />
      <h3 className="m-0 break-words font-display text-lg font-bold">{playlist.name}</h3>
      <p className="m-0 text-[0.9375rem] text-muted">
        <Trans
          id="music.page.forClass"
          slots={{ link: <Link to={`/classes/${playlist.classId}`}>{playlist.classTitle}</Link> }}
        />
      </p>
      {playlist.note !== null ? <p className="m-0 text-[0.8125rem] text-muted">{playlist.note}</p> : null}
      {playlist.link !== null ? (
        <Button asChild variant="secondary" className="self-start">
          <a href={playlist.link} target="_blank" rel="noopener noreferrer">
            {t('music.page.openSpotify')}
          </a>
        </Button>
      ) : null}
    </div>
  )
}

/** The MOWL playlists as initials tiles. */
export function MowlTiles({ playlists }: { playlists: Playlist[] }) {
  const t = useT()
  return (
    <ul className="m-0 grid list-none grid-cols-2 gap-4 p-0 sm:grid-cols-3 lg:grid-cols-5">
      {playlists.map((p) => {
        const name = p.name || t('music.page.untitled')
        return (
          <li key={p.spotifyPlaylistId} className={`${TILE} ${LIFT} gap-2 p-3`}>
            <span
              aria-hidden="true"
              className="flex aspect-square w-full items-center justify-center rounded-sm bg-raised font-display text-2xl font-extrabold text-muted"
            >
              {initials(name)}
            </span>
            <span className="break-words text-[0.9375rem] font-medium text-text">{name}</span>
          </li>
        )
      })}
    </ul>
  )
}

/** Shown when the MOWL playlists cannot be listed: ghost tiles, one sentence and the connect link. */
export function MowlEmptyState() {
  const t = useT()
  return (
    <div className="flex flex-wrap items-center gap-7 rounded-xl border border-line bg-surface p-6 shadow-card sm:p-9">
      <div className="flex gap-2.5" aria-hidden="true">
        <span className="size-16 rounded-md border border-dashed border-line bg-raised" />
        <span className="size-16 rounded-md border border-dashed border-line bg-raised opacity-70" />
        <span className="size-16 rounded-md border border-dashed border-line bg-raised opacity-40" />
      </div>
      <p className="m-0 min-w-0 flex-[1_1_16rem] text-[0.9375rem] leading-relaxed text-muted">
        {t('music.page.mowlFailed')}
      </p>
      <Button asChild variant="secondary">
        <Link to="/account">{t('music.page.mowlConnect')}</Link>
      </Button>
    </div>
  )
}
