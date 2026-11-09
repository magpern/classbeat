import type { Song } from '../../api/songs'
import { useT } from '../../i18n/I18n'
import { minutes, roleLabel } from '../../lib/songs'
import { Chip, ChipButton } from '../ui/chip'
import { ListRow } from '../ui/list-row'
import { CoverArt } from './CoverArt'

const LIST = 'm-0 flex list-none flex-col divide-y divide-line overflow-hidden rounded-lg border border-line bg-surface p-0'
// On a phone the action drops under the text, so a long title keeps the whole width of the row.
const ROW = 'gap-3 px-4 max-sm:flex-wrap sm:gap-4 sm:px-5'

/**
 * The songs of a list as numbered `ListRow`s (ADR 0028 section 2): the number and album cover,
 * the title with a "New" mark for a song that was not in the earlier list, the artist, then role,
 * BPM and length and, when `marks` is given, a Replace mark per song. Every text is rendered as
 * text and no id is shown. The cover comes through `CoverArt` only (ADR 0013 section 9), or its
 * initials tile.
 */
export function SongRows({
  songs,
  marks,
  onToggle,
  disabled = false,
}: {
  songs: readonly Song[]
  marks?: ReadonlySet<number>
  onToggle?: (position: number) => void
  disabled?: boolean
}) {
  const t = useT()
  return (
    <ol className={LIST} role="list" aria-label={t('songs.list.label')}>
      {songs.map((song) => (
        <ListRow
          key={song.position}
          data-testid="song"
          className={ROW}
          leading={
            <span className="flex shrink-0 items-center gap-3">
              <span className="w-6 text-right text-muted">{song.position}</span>
              <CoverArt src={song.cover} label={song.title} />
            </span>
          }
          title={
            <span className="break-words" data-testid="song-title">
              {song.title}
            </span>
          }
          badge={song.isNew ? <Chip tone="ok">{t('songs.list.new')}</Chip> : null}
          description={
            <>
              <span className="block break-words">{song.artist}</span>
              <span className="block">
                {roleLabel(song.role)}, {t('songs.list.bpm', { bpm: Math.round(song.bpm) })}, {minutes(song.lengthSec)}
              </span>
              {song.openSpotify !== null ? (
                <a
                  href={song.openSpotify}
                  target="_blank"
                  rel="noopener noreferrer"
                  aria-label={t('songs.list.openSpotifyLabel', { title: song.title })}
                  className="text-[0.8125rem] font-medium"
                >
                  {t('songs.list.openSpotify')}
                </a>
              ) : null}
            </>
          }
          action={
            marks !== undefined && onToggle !== undefined ? (
              <span className="max-sm:flex max-sm:basis-full max-sm:justify-end">
                <ChipButton
                  pressed={marks.has(song.position)}
                  disabled={disabled}
                  aria-label={t('songs.list.replaceLabel', { title: song.title })}
                  onClick={() => {
                    onToggle(song.position)
                  }}
                >
                  {t('songs.list.replace')}
                </ChipButton>
              </span>
            ) : null
          }
        />
      ))}
    </ol>
  )
}
