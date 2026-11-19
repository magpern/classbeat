import greenIcon from '../../assets/brand/Primary_Logo_Green_RGB.svg'

/**
 * Spotify's official green icon (the 2024 logo-icon package, unmodified, from our own bundle) in the
 * bottom-right corner of a playlist cover in the New class picker (ADR 0013 section 9). Decorative:
 * the playlist's name is the tile's label. The parent shows it only while the cover image shows.
 */
export function SpotifyCoverBadge() {
  return (
    <img
      src={greenIcon}
      alt=""
      aria-hidden="true"
      draggable={false}
      data-testid="spotify-badge"
      className="pointer-events-none absolute bottom-1.5 right-1.5 size-6 sm:size-7"
    />
  )
}
