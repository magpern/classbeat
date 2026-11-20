import { Trans, useT } from '../../i18n/I18n'
import { Link } from '../../router/Router'
import type { MadePlaylist } from '../../api/music'
import { CoverMosaic } from '../songs/CoverArt'
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
