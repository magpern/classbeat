import { lazy, Suspense, useEffect, useState } from 'react'
import { PLAYER_PATH, getPlayerStatus, type PlayerStatus } from '../../../api/player'
import { useT } from '../../../i18n/I18n'
import { useRouter } from '../../../router/Router'
import { Button } from '../../ui/button'
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '../../ui/dialog'
import { ConnectionTile } from './ConnectionTile'

// The dialog's body loads when the dialog opens.
const PlayerConnection = lazy(() => import('./PlayerConnection').then((m) => ({ default: m.PlayerConnection })))

/**
 * The classbeat-player row of Account, Connections (ADR 0034 section 6) and its dialog. The row is
 * left out while the player is switched off and nothing is connected. `/account/player`, where the
 * player's link returns (`?result=`), opens the dialog; closing it goes back to
 * `/account#connections`, so the result is not shown twice.
 */
export function PlayerRow() {
  const t = useT()
  const { path, navigate } = useRouter()
  const fromAddress = path === PLAYER_PATH
  const [status, setStatus] = useState<PlayerStatus | null>(null)
  const [open, setOpen] = useState(fromAddress)

  // A link to /account/player while Connections is already shown opens the dialog too.
  const [wasFromAddress, setWasFromAddress] = useState(fromAddress)
  if (fromAddress !== wasFromAddress) {
    setWasFromAddress(fromAddress)
    if (fromAddress) setOpen(true)
  }

  useEffect(() => {
    const controller = new AbortController()
    getPlayerStatus(controller.signal).then(
      (found) => {
        if (!controller.signal.aborted) setStatus(found)
      },
      () => undefined, // no player row; the rest of the list still works
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
  const offered = status !== null && (!notConnected || status.linkEnabled || fromAddress)

  return (
    <>
      {offered ? (
        <ConnectionTile
          testId="connection-player"
          mark="P"
          name={t('player.name')}
          optional
          state={notConnected ? 'none' : status.status === 'connected' ? 'connected' : 'again'}
          pill={
            notConnected ? (
              t('conn.notConnected')
            ) : (
              <span data-testid="player-status">
                {status.status === 'connected' ? t('mowl.statusConnected') : t('mowl.statusAgain')}
              </span>
            )
          }
          detail={t('conn.player.purpose')}
          action={
            <Button
              variant="secondary"
              aria-label={notConnected ? t('player.connect') : t('player.manage')}
              onClick={() => {
                setOpen(true)
              }}
            >
              {notConnected ? t('conn.connect') : t('conn.manage')}
            </Button>
          }
        />
      ) : null}
      <Dialog
        open={open && status !== null}
        onOpenChange={(next) => {
          if (!next) close()
        }}
      >
        <DialogContent className="w-[min(36rem,calc(100%-2rem))]">
          <DialogTitle>{t('player.name')}</DialogTitle>
          <DialogDescription>{t('conn.player.purpose')}</DialogDescription>
          {status !== null ? (
            <Suspense fallback={<p>{t('common.loading')}</p>}>
              <PlayerConnection status={status} onStatus={setStatus} />
            </Suspense>
          ) : null}
        </DialogContent>
      </Dialog>
    </>
  )
}
