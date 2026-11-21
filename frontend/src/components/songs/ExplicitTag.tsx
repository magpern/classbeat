import { useT } from '../../i18n/I18n'

/**
 * The boxed "E" of the Material Symbols `explicit` glyph, from Google's Material Design icons
 * (Apache License 2.0), drawn inline so no icon font or request is needed. Decorative on its own:
 * `ExplicitTag` gives it the accessible name.
 */
function ExplicitGlyph() {
  return (
    <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor" aria-hidden="true" focusable="false">
      <path d="M19 3H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zm-4 6h-4v2h4v2h-4v2h4v2H9V7h6v2z" />
    </svg>
  )
}

/**
 * A small "Explicit lyrics" tag next to a song's other marks (#1340). Report only: it tells the
 * instructor, it never changes a list. The accessible label and the tooltip are the same text.
 */
export function ExplicitTag() {
  const label = useT()('songs.list.explicit')
  return (
    <span
      role="img"
      aria-label={label}
      title={label}
      data-testid="song-explicit"
      className="inline-flex shrink-0 items-center text-muted"
    >
      <ExplicitGlyph />
    </span>
  )
}
