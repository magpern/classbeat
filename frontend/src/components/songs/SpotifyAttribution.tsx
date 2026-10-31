import blackLogo from '../../assets/brand/Full_Logo_Black_RGB.svg'
import whiteLogo from '../../assets/brand/Full_Logo_White_RGB.svg'

/**
 * Says that the songs, artists and covers come from Spotify (Spotify's Design guidelines,
 * "Attribution"): the official full logo, served from our own bundle, unmodified. The white file
 * shows on the dark theme and the black one on the light theme (a monochrome logo for any other
 * background). Only the shown image is in the accessibility tree (the other is display:none), so
 * both carry the alt text "Spotify"; see `.spotify-attribution` in app.css for the size and clear space.
 */
export function SpotifyAttribution() {
  return (
    <p className="spotify-attribution" data-testid="spotify-attribution">
      <img className="spotify-attribution__dark" src={whiteLogo} alt="Spotify" />
      <img className="spotify-attribution__light" src={blackLogo} alt="Spotify" />
    </p>
  )
}
