import { Link } from '../../../router/Router'
import { useT } from '../../../i18n/I18n'
import { clock } from '../../../lib/workout'
import { describeFailure } from '../../../preview/spotifyErrorModel'
import type { SpotifyView } from '../../../preview/spotifyConnectTransport'
import { cursorSentence, noteSentence, stateSentence } from '../../../preview/spotifyStatusModel'
import { SPOTIFY_PATH } from '../../account/connections/SpotifyRow'
import { Notice } from '../../forms'
import { Button } from '../../ui/button'

/**
 * What the Spotify player is doing, in words: the state or the failure (never a code), the way
 * forward when there is one, a note for a seek or a device switch, and the cursor's band.
 */
export function SpotifyStatus({ view, onReload }: { view: SpotifyView; onReload: () => void }) {
  const t = useT()
  const device = view.device?.name ?? t('preview.state.device.none')
  const headline = stateSentence(view, device)
  const note = noteSentence(view, device)
  const cursor = cursorSentence(view, clock((view.classMs ?? 0) / 1000))
  const action = view.failure === null ? null : describeFailure(view.failure).action
  return (
    <div className="flex flex-col gap-2" data-testid="spotify-status" data-state={view.state.status}>
      <Notice kind={headline.tone === 'error' ? 'error' : 'info'}>{t(headline.key, headline.params)}</Notice>
      {action === 'reconnect' ? (
        <p className="m-0">
          <Link className="button" to={SPOTIFY_PATH} data-testid="spotify-reconnect">
            {t('preview.action.reconnect')}
          </Link>
        </p>
      ) : null}
      {action === 'sign_in' ? (
        <p className="m-0">
          <Link className="button" to="/signin">
            {t('preview.action.sign_in')}
          </Link>
        </p>
      ) : null}
      {action === 'reload' ? (
        <p className="m-0">
          <Button variant="secondary" onClick={onReload}>
            {t('preview.action.reload')}
          </Button>
        </p>
      ) : null}
      {action === 'open_spotify' ? <p className="m-0 text-muted">{t('preview.action.open_spotify')}</p> : null}
      {note === null ? null : (
        <p className="m-0 text-muted" role="status" data-testid="spotify-note">
          {t(note.key, note.params)}
        </p>
      )}
      <p className="m-0 text-muted" data-testid="spotify-cursor" aria-live="off">
        {t(cursor.key, cursor.params)}
      </p>
    </div>
  )
}
