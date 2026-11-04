import { useState } from 'react'
import { cn } from '../../lib/cn'

/**
 * The one place the app shows a cover (ADR 0013 section 9 and its amendment of 2026-11-03, #537).
 * It is also the one source file allowed to name the cover hosts (`no-third-party-hosts.test.ts`),
 * and the CSP's `img-src` allows only our origin and path prefixes on those hosts.
 *
 * - An album cover (`kind="album"`, the default: song lists) is shown only when the whole string
 *   is `COVER_HOST`, `/image/` and 40 lowercase hex digits.
 * - A playlist cover (`kind="playlist"`: the New class picker) may also be Spotify's mosaic of
 *   four album images at 300 or 640 px on `MOSAIC_HOST`, or an uploaded cover on one of
 *   `UPLOAD_HOSTS`, each a whole-string match.
 * - The server checks the same before it stores or returns one; this is a second check, so a page
 *   never asks any other host or path.
 * - The image is loaded lazily, with no referrer, and shown unaltered (no crop, overlay, blur or
 *   tint) with a 4 px radius (8 px when large).
 * - No cover, a URL that fails the check, or an image that does not load gets a neutral tile with
 *   initials instead.
 *
 * The cover is decorative: the title and artist (or the playlist name) are always in the text
 * beside it.
 */
const COVER_HOST = 'https://i.scdn.co'
const MOSAIC_HOST = 'https://mosaic.scdn.co'
const UPLOAD_HOSTS = ['https://image-cdn-ak.spotifycdn.com', 'https://image-cdn-fa.spotifycdn.com']
const HEX40 = '[0-9a-f]{40}'
const escaped = (host: string) => host.replaceAll('.', '\\.')
const ALBUM = `${escaped(COVER_HOST)}/image/${HEX40}`
const COVER_URL = new RegExp(`^${ALBUM}$`)
const PLAYLIST_COVER_URL = new RegExp(
  `^(?:${ALBUM}|${escaped(MOSAIC_HOST)}/(?:300|640)/(?:${HEX40}){4}|(?:${UPLOAD_HOSTS.map(escaped).join('|')})/image/${HEX40})$`,
)

export function isCoverUrl(value: unknown): value is string {
  // In JavaScript `$` (no `m` flag) matches only at the very end: a trailing newline fails.
  return typeof value === 'string' && COVER_URL.test(value)
}

/** A playlist cover: the album pin, a 300 or 640 px mosaic, or an uploaded cover (#537). */
export function isPlaylistCoverUrl(value: unknown): value is string {
  return typeof value === 'string' && PLAYLIST_COVER_URL.test(value)
}

/** Up to two letters for the neutral tile: the first letters of the first two words. */
export function initials(label: string): string {
  const words = label.split(/\s+/u).filter((word) => /\p{L}|\p{N}/u.test(word))
  const letters = words.slice(0, 2).map((word) => {
    const first = [...word].find((c) => /\p{L}|\p{N}/u.test(c))
    return first ?? ''
  })
  return letters.join('').toLocaleUpperCase()
}

const SIZES = {
  sm: 'size-11 rounded-[4px] text-[0.8125rem]',
  md: 'size-16 rounded-[4px] text-base',
  lg: 'size-full rounded-[8px] text-2xl',
} as const

export function CoverArt({
  src,
  label,
  size = 'sm',
  kind = 'album',
  className,
}: {
  /** The song's (or playlist's) `cover` from the API, or null. */
  src: string | null
  /** The song's title (and artist), or the playlist's name: its initials fill the tile when there is no cover. */
  label: string
  size?: keyof typeof SIZES
  /** Which pin the URL must match: `album` (song lists) or `playlist` (the New class picker). */
  kind?: 'album' | 'playlist'
  className?: string
}) {
  const [failed, setFailed] = useState<string | null>(null)
  const valid = kind === 'playlist' ? isPlaylistCoverUrl(src) : isCoverUrl(src)
  const box = cn('shrink-0 overflow-hidden', SIZES[size], className)
  if (!valid || src === null || failed === src) {
    return (
      <span
        className={cn(box, 'inline-flex items-center justify-center bg-raised font-display font-bold text-muted')}
        aria-hidden="true"
        data-testid="cover-tile"
      >
        {initials(label)}
      </span>
    )
  }
  return (
    <img
      className={cn(box, 'block object-cover')}
      src={src}
      alt=""
      referrerPolicy="no-referrer"
      loading="lazy"
      decoding="async"
      draggable={false}
      onError={() => {
        setFailed(src)
      }}
    />
  )
}

/**
 * The 2x2 mosaic of a song list's first four covers (ADR 0013 section 5, "Songs for your class").
 * Fewer than four songs repeat none: the empty cells are neutral tiles.
 */
export function CoverMosaic({
  songs,
  className,
}: {
  songs: readonly { cover: string | null; title: string }[]
  className?: string
}) {
  const four = [0, 1, 2, 3].map((i) => songs[i] ?? null)
  return (
    <div
      className={cn('grid size-32 shrink-0 grid-cols-2 grid-rows-2 overflow-hidden rounded-[8px]', className)}
      aria-hidden="true"
      data-testid="cover-mosaic"
    >
      {four.map((song, i) =>
        song === null ? (
          <span key={i} className="bg-raised" />
        ) : (
          <CoverArt key={i} src={song.cover} label={song.title} size="lg" className="rounded-none" />
        ),
      )}
    </div>
  )
}
