import { SpotifyConnection } from '../components/account/Spotify'
import { RequireAuth } from '../components/RequireAuth'
import { Link } from '../router/Router'

/** /account/spotify: where the Spotify callback ends (`?result=connected` or a category). */
export function SpotifyPage() {
  return (
    <RequireAuth>
      <SpotifyConnection />
      <p>
        <Link to="/account">Back to your account</Link>
      </p>
    </RequireAuth>
  )
}
