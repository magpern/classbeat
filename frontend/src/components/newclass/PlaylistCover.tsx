import { useState } from 'react'
import { ConnectionMark } from '../account/connections/ConnectionMark'
import { SPOTIFY_ICON } from '../account/connections/spotifyIcon'
import { CoverArt, isPlaylistCoverUrl } from '../songs/CoverArt'

/**
 * A playlist's cover in a picker tile, with the Spotify icon in its top-left corner while the cover
 * is really shown (ADR 0013 section 9, #1288). The badge says the cover comes from Spotify: the
 * official icon from our own bundle, unmodified, in the file for the theme (`.brand-icon` in app.css).
 * It sits on a round chip of the page's own surface colour, so it reads on any cover and keeps its
 * clear space. A cover that fails the URL check or fails to load is a neutral initials tile
 * (`CoverArt`) and has no badge. Everything here is decorative: the tile's name is the radio's label.
 */
export function PlaylistCover({ src, name }: { src: string; name: string }) {
  const [failed, setFailed] = useState<string | null>(null)
  const shown = isPlaylistCoverUrl(src) && failed !== src
  return (
    <span aria-hidden="true" className="relative block aspect-square w-full" data-testid="playlist-cover">
      <CoverArt
        src={src}
        label={name}
        size="lg"
        kind="playlist"
        onLoadError={() => {
          setFailed(src)
        }}
      />
      {shown && (
        <span
          className="pointer-events-none absolute top-2 left-2 flex size-9 items-center justify-center rounded-full bg-surface ring-1 ring-line"
          data-testid="spotify-cover-badge"
        >
          <ConnectionMark mark={SPOTIFY_ICON} />
        </span>
      )}
    </span>
  )
}
