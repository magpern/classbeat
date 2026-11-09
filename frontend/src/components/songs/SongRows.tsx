import type { Song } from '../../api/songs'
import { useT } from '../../i18n/I18n'
import { minutes, roleLabel } from '../../lib/songs'
import { Chip, ChipButton } from '../ui/chip'
import { CoverArt } from './CoverArt'

/**
 * The songs of a list as numbered rows: title, artist, role, BPM and length, a "New" mark for a
 * song that was not in the earlier list and, when `marks` is given, a Replace mark per song.
 * Every text is rendered as text and no id is shown. Each row starts with the song's album
 * cover, through `CoverArt` only (ADR 0013 section 9), or its initials tile.
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
    <ol className="m-0 flex list-none flex-col gap-2 p-0" role="list" aria-label={t('songs.list.label')}>
      {songs.map((song) => {
        const marked = marks?.has(song.position) === true
        return (
          <li
            key={song.position}
            className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 rounded-md border border-line bg-surface p-3"
            data-testid="song"
          >
            <div className="flex min-w-0 flex-1 basis-64 items-center gap-3">
              <span className="w-8 shrink-0 text-right text-muted">
                {song.position}
              </span>
              <CoverArt src={song.cover} label={song.title} />
              <div className="min-w-0 flex-1">
                <p className="m-0 break-words font-medium text-text">{song.title}</p>
                <p className="m-0 break-words text-[0.9375rem] text-muted">{song.artist}</p>
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
              </div>
            </div>
            <div className="ml-auto flex flex-wrap items-center justify-end gap-3">
              <Chip>{roleLabel(song.role)}</Chip>
              <span className="text-[0.9375rem] text-muted">{t('songs.list.bpm', { bpm: Math.round(song.bpm) })}</span>
              <span className="text-[0.9375rem] text-muted">{minutes(song.lengthSec)}</span>
              {song.isNew ? <Chip tone="ok">{t('songs.list.new')}</Chip> : null}
              {marks !== undefined && onToggle !== undefined ? (
                <ChipButton
                  pressed={marked}
                  disabled={disabled}
                  aria-label={t('songs.list.replaceLabel', { title: song.title })}
                  onClick={() => {
                    onToggle(song.position)
                  }}
                >
                  {t('songs.list.replace')}
                </ChipButton>
              ) : null}
            </div>
          </li>
        )
      })}
    </ol>
  )
}
