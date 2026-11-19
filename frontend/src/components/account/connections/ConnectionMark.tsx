/** An official brand icon as two files of one icon: the black one for light surfaces, the white one for dark ones. */
export interface BrandIcon {
  light: string
  dark: string
}

/** What a connection tile or row shows in its square: a letter monogram, or a brand icon. */
export type ConnectionMarkContent = string | BrandIcon

/**
 * The content of the square. A letter is plain text; a brand icon is the official file, unmodified and
 * monochrome, half the square wide but never under 24px (so at least 21px high, Spotify's minimum),
 * which leaves about half its height as clear space (Spotify's guidelines). The image is decorative
 * (the name sits next to it); the theme picks the black or the white file in CSS (`.brand-icon` in
 * app.css), so a theme switch needs no re-render.
 */
export function ConnectionMark({ mark }: { mark: ConnectionMarkContent }) {
  if (typeof mark === 'string') return <>{mark}</>
  return (
    <>
      <img className="brand-icon brand-icon__dark" src={mark.dark} alt="" aria-hidden="true" />
      <img className="brand-icon brand-icon__light" src={mark.light} alt="" aria-hidden="true" />
    </>
  )
}
