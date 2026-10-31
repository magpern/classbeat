import type { Song } from '../../api/songs'

/**
 * The songs under the time axis, as alternating blocks that start where each song starts in the
 * class (the lengths before it less the crossfade at every change, `Song.startSec`). A song's
 * block runs to the next song's start, so the blocks meet, and the last one to the end of the
 * class. Titles are text; the strip is hidden by its caller while there is no song list.
 */
export function SongStrip({ songs, total, left }: { songs: Song[]; total: number; left: number }) {
  const share = (sec: number) => Math.min(Math.max(sec / total, 0), 1)
  return (
    <ol
      data-testid="song-strip"
      aria-label="Songs"
      className="relative m-0 mt-1 h-7 list-none overflow-hidden p-0 text-[0.75rem]"
      style={{ marginLeft: `${String(left * 100)}%` }}
    >
      {songs.map((song, i) => {
        const from = share(song.startSec)
        const to = share(songs[i + 1]?.startSec ?? song.startSec + song.lengthSec)
        return (
          <li
            key={song.position}
            data-start={song.startSec}
            title={`${song.title}, ${String(song.bpm)} BPM`}
            className={`absolute top-0 bottom-0 min-w-0 overflow-hidden whitespace-nowrap px-1 py-1 text-ellipsis ${i % 2 === 0 ? 'bg-stage-grid' : 'bg-stage'} border border-stage-grid text-stage-text`}
            style={{ left: `${String(from * 100)}%`, width: `${String((to - from) * 100)}%` }}
          >
            {song.title} <span className="text-stage-muted">{String(song.bpm)}</span>
          </li>
        )
      })}
    </ol>
  )
}
