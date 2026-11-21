import { useId } from 'react'
import type { SpotifyStatus } from '../../api/spotify'
import { useT } from '../../i18n/I18n'

// ADR 0042 section 7.1: the opt-in listening preview of a connected Spotify account. Enabling it
// is a re-consent through the same PKCE flow with the user's own Client ID; nothing changes until
// Spotify's consent succeeds, and a cancel leaves the connection as it is. No token is ever here.

/** The listening-preview part of a connected Spotify dialog. */
export function SpotifyPreview({
  status,
  busy,
  onEnable,
}: {
  status: SpotifyStatus
  busy: boolean
  /** Starts the re-consent with the stored Client ID and the playback scopes. */
  onEnable: (clientId: string) => void
}) {
  const t = useT()
  const titleId = useId()
  const clientId = status.clientId
  return (
    <section aria-labelledby={titleId} className="flex flex-col gap-3" data-testid="spotify-preview">
      <h3 id={titleId} className="m-0 font-display text-lg font-bold">
        {t('spot.previewTitle')}
      </h3>
      {status.previewGranted ? (
        <p className="m-0">{t('spot.previewOn')}</p>
      ) : (
        <>
          <p className="m-0">{t('spot.previewOff')}</p>
          <p className="m-0 text-muted">{t('spot.previewNeeds')}</p>
          <p className="m-0 text-muted">{t('spot.previewNote')}</p>
          {clientId !== null ? (
            <div>
              <button
                className="button button--secondary"
                type="button"
                disabled={busy}
                onClick={() => {
                  onEnable(clientId)
                }}
              >
                {t('spot.previewEnable')}
              </button>
            </div>
          ) : null}
        </>
      )}
    </section>
  )
}
