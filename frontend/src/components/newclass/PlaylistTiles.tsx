import { useId } from 'react'
import type { Playlist } from '../../api/jobs'

/** The playlist's initials for its tile. There are no playlist covers (ADR 0013 section 9). */
export function initials(name: string): string {
  const words = name.split(/\s+/).filter((w) => /[\p{L}\p{N}]/u.test(w))
  const letters = words.slice(0, 2).map((w) => Array.from(w.replace(/^[^\p{L}\p{N}]+/u, ''))[0] ?? '')
  const text = letters.join('').toUpperCase()
  return text === '' ? '♪' : text
}

/**
 * The MOWL playlists as a tile grid. Real radio buttons in one group, so Tab enters the grid and
 * the arrow keys move between tiles.
 */
export function PlaylistTiles({
  playlists,
  value,
  onChange,
}: {
  playlists: Playlist[]
  value: string
  onChange: (id: string) => void
}) {
  const group = useId()
  return (
    <div className="flex flex-col gap-2">
      <span id={`${group}-label`} className="text-[0.9375rem] font-medium text-text">
        Playlist
      </span>
      <div
        role="radiogroup"
        aria-labelledby={`${group}-label`}
        aria-describedby={`${group}-hint`}
        className="grid grid-cols-2 gap-3 sm:grid-cols-3"
      >
        {playlists.map((p) => {
          const name = p.name || 'Untitled playlist'
          return (
            <label
              key={p.spotifyPlaylistId}
              className="relative flex min-h-11 cursor-pointer flex-col gap-2 rounded-md border border-line bg-raised p-3 hover:bg-line has-checked:border-brand has-checked:bg-surface has-checked:outline-2 has-checked:outline-brand has-focus-visible:outline-2 has-focus-visible:outline-offset-2 has-focus-visible:outline-brand"
            >
              <input
                type="radio"
                name={`${group}-playlist`}
                value={p.spotifyPlaylistId}
                checked={value === p.spotifyPlaylistId}
                onChange={() => {
                  onChange(p.spotifyPlaylistId)
                }}
                className="sr-only"
              />
              <span
                aria-hidden="true"
                className="flex aspect-square w-full items-center justify-center rounded-sm bg-surface font-display text-2xl font-extrabold text-muted"
              >
                {initials(name)}
              </span>
              <span className="break-words text-[0.9375rem] font-medium text-text">{name}</span>
            </label>
          )
        })}
      </div>
      <p id={`${group}-hint`} className="m-0 text-[0.8125rem] text-muted">
        classbeat adds this playlist to your MOWL music so it can read the songs. It stays private to you.
      </p>
    </div>
  )
}
