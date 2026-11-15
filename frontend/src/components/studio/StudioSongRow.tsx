import type { StudioSong } from '../../api/studioSongs'
import { useT } from '../../i18n/I18n'
import { minutes, roleLabel } from '../../lib/songs'
import { CoverArt } from '../songs/CoverArt'
import { Button } from '../ui/button'
import { Chip } from '../ui/chip'

/**
 * One song of a studio list: the pick with SwedeBeat's duration and BPM, a gap marked as such, and
 * a swap as original → replacement with its reason, with Undo and Ask for another. Every text is
 * rendered as text; the API sends no Spotify id and no catalogue reference.
 */
export function StudioSongRow({
  song,
  disabled,
  onUndo,
  onAnother,
}: {
  song: StudioSong
  disabled: boolean
  onUndo: (position: number) => void
  onAnother: (position: number) => void
}) {
  const t = useT()
  const { swap, swedebeat } = song
  return (
    <li
      className="flex flex-wrap items-center gap-3 rounded-md border border-line bg-surface p-3"
      data-testid="studio-song"
      data-gap={song.gap ? 'true' : 'false'}
    >
      <span className="w-6 text-right text-muted">{song.position + 1}</span>
      <CoverArt src={song.cover} label={song.title} />
      <div className="min-w-40 flex-1 basis-40">
        <p className="m-0 break-words font-medium text-text">{song.title}</p>
        <p className="m-0 break-words text-[0.9375rem] text-muted">
          {song.artist} · {roleLabel(song.role)}
        </p>
        {swedebeat !== null ? (
          <p className="m-0 break-words text-[0.9375rem] text-muted">
            {t('studio.facts', { duration: minutes(swedebeat.durationSec), bpm: Math.round(swedebeat.bpm) })}
          </p>
        ) : null}
        {song.gap ? <p className="m-0 text-[0.9375rem] text-bad">{t('studio.gapText')}</p> : null}
        {swap !== null ? (
          <div className="mt-1" data-testid="studio-swap">
            <p className="m-0 break-words text-[0.9375rem] text-text">
              {t('studio.swap', {
                original: `${swap.original.title}, ${swap.original.artist}`,
                replacement: `${swap.replacement.title}, ${swap.replacement.artist}`,
              })}
            </p>
            <p className="m-0 text-[0.9375rem] text-muted">
              {swap.undone
                ? t('studio.swapUndone', { original: swap.original.title })
                : t(`studio.swapReason.${swap.reason}`)}
            </p>
          </div>
        ) : null}
      </div>
      {song.gap ? <Chip tone="bad">{t('studio.gap')}</Chip> : null}
      <div className="flex flex-wrap gap-2">
        {song.canUndo ? (
          <Button
            variant="secondary"
            disabled={disabled}
            aria-label={t('studio.undoLabel', { title: song.title })}
            onClick={() => {
              onUndo(song.position)
            }}
          >
            {t('studio.undo')}
          </Button>
        ) : null}
        {song.canAskAnother ? (
          <Button
            variant="secondary"
            disabled={disabled}
            aria-label={t('studio.anotherLabel', { title: song.title })}
            onClick={() => {
              onAnother(song.position)
            }}
          >
            {t('studio.another')}
          </Button>
        ) : null}
      </div>
    </li>
  )
}
