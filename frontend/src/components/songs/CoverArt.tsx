import { useState } from 'react'
import { cn } from '../../lib/cn'

/**
 * The one place the app shows an album cover (ADR 0013 section 9). It is also the one source
 * file allowed to name the cover host (`no-third-party-hosts.test.ts`), and the CSP's `img-src`
 * allows only our origin and that host.
 *
 * - The URL is shown only when the whole string is `COVER_HOST`, `/image/` and 40 lowercase hex
 *   digits. The server checks the same before it stores and before it returns one; this is a
 *   second check, so a page never asks any other host.
 * - The image is loaded lazily, with no referrer, and shown unaltered (no crop, overlay, blur or
 *   tint) with a 4 px radius (8 px when large).
 * - A song without a cover, with a URL that fails the check, or whose image does not load gets a
 *   neutral tile with initials instead.
 *
 * The cover is decorative: the title and artist are always in the text beside it.
 */
const COVER_HOST = 'https://i.scdn.co'
const COVER_URL = new RegExp(`^${COVER_HOST.replaceAll('.', '\\.')}/image/[0-9a-f]{40}$`)

export function isCoverUrl(value: unknown): value is string {
  // In JavaScript `$` (no `m` flag) matches only at the very end: a trailing newline fails.
  return typeof value === 'string' && COVER_URL.test(value)
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
  className,
}: {
  /** The song's `cover` from the API, or null. */
  src: string | null
  /** The song's title (and artist): its initials fill the tile when there is no cover. */
  label: string
  size?: keyof typeof SIZES
  className?: string
}) {
  const [failed, setFailed] = useState<string | null>(null)
  const show = isCoverUrl(src) && failed !== src
  const box = cn('shrink-0 overflow-hidden', SIZES[size], className)
  if (!show) {
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
