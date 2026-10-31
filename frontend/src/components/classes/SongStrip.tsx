import type { Song } from '../../api/songs'

/**
 * The songs under the time axis, as alternating blocks as wide as each song's time in the class.
 * The songs play one after the other from the start. Titles are text; the strip is hidden by its
 * caller while there is no song list.
 */
export function SongStrip({ songs, total, left }: { songs: Song[]; total: number; left: number }) {
  const widths = songs.map((s) => Math.min((s.lengthSec / total) * 100, 100))
  return (
    <ol
      data-testid="song-strip"
      aria-label="Songs"
      className="m-0 mt-1 flex list-none overflow-hidden p-0 text-[0.75rem]"
      style={{ marginLeft: `${String(left * 100)}%` }}
    >
      {songs.map((song, i) => (
        <li
          key={song.position}
          title={`${song.title}, ${String(song.bpm)} BPM`}
          className={`min-w-0 shrink-0 overflow-hidden whitespace-nowrap px-1 py-1 text-ellipsis ${i % 2 === 0 ? 'bg-stage-grid' : 'bg-stage'} border border-stage-grid text-stage-text`}
          style={{ width: `${String((widths[i] ?? 0) * (1 - left))}%` }}
        >
          {song.title} <span className="text-stage-muted">{String(song.bpm)}</span>
        </li>
      ))}
    </ol>
  )
}
