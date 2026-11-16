import { Trans, useT } from '../../i18n/I18n'
import { Link } from '../../router/Router'
import { Notice } from '../forms'
import type { SpotifyGate } from './spotifyGate'

/** Why a class with music cannot be started yet, with the link to put it right (roadmap Amendment 25). */
export function SpotifyPrompt({ need }: { need: SpotifyGate['need'] }) {
  const t = useT()
  return (
    <Notice>
      <span data-testid="spotify-prompt" data-need={need}>
        {need === 'connect' ? (
          <Trans id="start.spotify.needed" slots={{ link: <Link to="/account/spotify">{t('music.connectSpotify')}</Link> }} />
        ) : (
          <Trans id="music.needsConsent" slots={{ link: <Link to="/account/spotify">{t('music.openSettings')}</Link> }} />
        )}
      </span>
    </Notice>
  )
}
