import { useT } from '../i18n/I18n'
import { SpotifyConnection } from '../components/account/Spotify'
import { RequireAuth } from '../components/RequireAuth'
import { Link } from '../router/Router'

/** /account/spotify: where the Spotify callback ends (`?result=connected` or a category). */
export function SpotifyPage() {
  const t = useT()
  return (
    <RequireAuth>
      <SpotifyConnection />
      <p>
        <Link to="/account">{t('spotifyPage.back')}</Link>
      </p>
    </RequireAuth>
  )
}
