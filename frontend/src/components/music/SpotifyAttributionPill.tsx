import { useT } from '../../i18n/I18n'
import { SpotifyAttribution } from '../songs/SpotifyAttribution'

/**
 * The one Spotify attribution of the Music page: the official logo, unchanged and at its own size
 * and clear space, then a short caption, in a pill under the page intro. It wraps on a narrow phone.
 */
export function SpotifyAttributionPill() {
  const t = useT()
  return (
    <div
      data-testid="spotify-attribution-pill"
      className="flex w-fit max-w-full flex-wrap items-center gap-x-3 rounded-full border border-line bg-surface px-5"
    >
      <SpotifyAttribution />
      <span className="py-3 text-sm text-muted">{t('music.page.attribution')}</span>
    </div>
  )
}
