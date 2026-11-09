import { useEffect, useState } from 'react'
import { getSpotifyStatus, type SpotifyStatus } from '../../../api/spotify'
import { useT } from '../../../i18n/I18n'
import { useRouter } from '../../../router/Router'
import { Button } from '../../ui/button'
import { ConnectionRow } from './ConnectionRow'
import { SpotifyDialog } from './SpotifyDialog'

/** The address that opens the Spotify dialog, and where Spotify's callback lands (`?result=`). */
export const SPOTIFY_PATH = '/account/spotify'

/**
 * The Spotify row of Account, Connections and its dialog. The row is left out while song picking
 * is switched off (no status). `/account/spotify` (links from Home, the music pages, the song
 * screens, and the return from Spotify) opens the dialog; closing it goes back to
 * `/account#connections`, so the callback result is not shown twice.
 */
export function SpotifyRow() {
  const t = useT()
  const { path, navigate } = useRouter()
  const fromAddress = path === SPOTIFY_PATH
  const [status, setStatus] = useState<SpotifyStatus | null>(null)
  const [open, setOpen] = useState(fromAddress)

  // A link to /account/spotify while Connections is already shown opens the dialog too.
  const [wasFromAddress, setWasFromAddress] = useState(fromAddress)
  if (fromAddress !== wasFromAddress) {
    setWasFromAddress(fromAddress)
    if (fromAddress) setOpen(true)
  }

  useEffect(() => {
    const controller = new AbortController()
    getSpotifyStatus(controller.signal).then(
      (found) => {
        if (!controller.signal.aborted) setStatus(found)
      },
      () => undefined, // no Spotify row; the rest of the list still works
    )
    return () => {
      controller.abort()
    }
  }, [])

  const close = () => {
    setOpen(false)
    if (fromAddress) navigate('/account#connections', { replace: true, scroll: false })
  }
  const notConnected = status?.status === 'not_connected'

  return (
    <>
      {status !== null ? (
        <ConnectionRow
          testId="connection-spotify"
          mark="S"
          name={t('spot.title')}
          optional
          status={
            notConnected ? (
              t('conn.spotify.purpose')
            ) : (
              <span data-testid="spotify-summary-status">
                {status.status === 'connected' ? t('mowl.statusConnected') : t('mowl.statusAgain')}
              </span>
            )
          }
          action={
            <Button
              variant="secondary"
              aria-label={notConnected ? t('spot.connect') : t('spot.manage')}
              onClick={() => {
                setOpen(true)
              }}
            >
              {notConnected ? t('conn.connect') : t('conn.manage')}
            </Button>
          }
        />
      ) : null}
      <SpotifyDialog open={open} onClose={close} onStatus={setStatus} />
    </>
  )
}
