import { useEffect, useId, useState } from 'react'
import { getPlaylistCovers, type Playlist } from '../../api/jobs'
import { CoverArt } from '../songs/CoverArt'

/**
 * The covers of the user's Spotify playlists for the picker, asked once when `enabled` (ADR 0013
 * section 9, amendment of 2026-11-03, #537). Until they arrive, and on any failure, the map is
 * empty and the tiles show initials: the picker never waits for covers and never shows an error.
 */
export function usePlaylistCovers(enabled: boolean): ReadonlyMap<string, string> {
  const [covers, setCovers] = useState<ReadonlyMap<string, string>>(() => new Map())
  useEffect(() => {
    if (!enabled) return
    const abort = new AbortController()
    getPlaylistCovers(abort.signal).then(setCovers, () => undefined)
    return () => {
      abort.abort()
    }
  }, [enabled])
  return covers
}

/** The playlist's initials for its tile, when it has no cover (ADR 0013 section 9). */
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
  covers,
}: {
  playlists: Playlist[]
  value: string
  onChange: (id: string) => void
  /** Covers by Spotify playlist id (`usePlaylistCovers`); a playlist without one shows initials. */
  covers?: ReadonlyMap<string, string>
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
              {covers?.has(p.spotifyPlaylistId) ? (
                <span aria-hidden="true" className="block aspect-square w-full" data-testid="playlist-cover">
                  <CoverArt src={covers.get(p.spotifyPlaylistId) ?? null} label={name} size="lg" kind="playlist" />
                </span>
              ) : (
                <span
                  aria-hidden="true"
                  className="flex aspect-square w-full items-center justify-center rounded-sm bg-surface font-display text-2xl font-extrabold text-muted"
                >
                  {initials(name)}
                </span>
              )}
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
