import type { Song } from '../../api/songs'
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
  return (
    <ol className="m-0 flex list-none flex-col gap-2 p-0" role="list" aria-label="Songs">
      {songs.map((song) => {
        const marked = marks?.has(song.position) === true
        return (
          <li
            key={song.position}
            className="flex flex-wrap items-center gap-3 rounded-md border border-line bg-surface p-3"
            data-testid="song"
          >
            <span className="w-8 text-right text-muted">
              {song.position}
            </span>
            <CoverArt src={song.cover} label={song.title} />
            <div className="min-w-0 flex-1">
              <p className="m-0 break-words font-medium text-text">{song.title}</p>
              <p className="m-0 break-words text-[0.9375rem] text-muted">{song.artist}</p>
            </div>
            <Chip>{roleLabel(song.role)}</Chip>
            <span className="text-[0.9375rem] text-muted">{Math.round(song.bpm)} BPM</span>
            <span className="text-[0.9375rem] text-muted">{minutes(song.lengthSec)}</span>
            {song.isNew ? <Chip tone="ok">New</Chip> : null}
            {marks !== undefined && onToggle !== undefined ? (
              <ChipButton
                pressed={marked}
                disabled={disabled}
                aria-label={`Replace ${song.title}`}
                onClick={() => {
                  onToggle(song.position)
                }}
              >
                Replace
              </ChipButton>
            ) : null}
          </li>
        )
      })}
    </ol>
  )
}
