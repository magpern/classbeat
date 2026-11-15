import { useState } from 'react'
import {
  disconnectPlayer,
  getPlayerStatus,
  startPlayerLink,
  type PlayerStatus,
} from '../../../api/player'
import { useGuard } from '../../../auth/prompts'
import type { CatalogueKey } from '../../../i18n/en'
import { useT } from '../../../i18n/I18n'
import { goToProvider } from '../../../lib/navigation'
import { useQuery } from '../../../router/Router'
import { Notice, useAction } from '../../forms'
import { removal } from '../../player/operations'
import { UncertainPlayer } from '../../player/UncertainPlayer'
import { Button } from '../../ui/button'
import { ConfirmDisconnect } from './ConfirmDisconnect'

const RESULTS: Record<string, CatalogueKey> = {
  connected: 'player.connected',
  wrong_account: 'player.result.wrong_account',
  upstream_unavailable: 'player.result.upstream_unavailable',
  rate_limited: 'player.result.rate_limited',
  link_in_progress: 'player.result.link_in_progress',
}

/** The words for the callback's `?result=`; a category this page does not know says only "not completed". */
function resultKey(result: string): CatalogueKey {
  return (Object.hasOwn(RESULTS, result) ? RESULTS[result] : undefined) ?? 'player.result.other'
}

/**
 * The body of the classbeat-player dialog (ADR 0034 sections 3 and 6). Not connected: what Connect
 * does and the button, which leaves for the player's own page (the guard asks for a recent sign-in
 * first). Connected or needing a reconnect: the status, Reconnect and Disconnect. `?result=` and
 * `?note=` are the callback's answer on `/account/player`. Nothing here shows a token or a link id.
 */
export function PlayerConnection({
  status,
  onStatus,
}: {
  status: PlayerStatus
  onStatus: (status: PlayerStatus) => void
}) {
  const t = useT()
  const guard = useGuard()
  const query = useQuery()
  const result = query.get('result')
  const [note, setNote] = useState<string | null>(null)
  const [confirming, setConfirming] = useState(false)
  const [version, setVersion] = useState(0)
  const action = useAction()

  const connect = () => {
    setNote(null)
    void action.run(async () => {
      goToProvider(await guard(() => startPlayerLink()))
    })
  }
  const disconnect = () => {
    setConfirming(false)
    setNote(null)
    void action.run(async () => {
      const done = await guard(() => disconnectPlayer())
      onStatus(await getPlayerStatus())
      setNote(t('player.disconnected') + (done.linkMayRemain ? ` ${t('player.linkMayRemain')}` : ''))
    })
  }

  const connected = status.status === 'connected'
  const needsReconnect = status.status === 'needs_reconnect'
  return (
    <div className="flex flex-col gap-3" data-testid="player-connection">
      {result !== null ? (
        <Notice kind={result === 'connected' ? 'success' : 'error'}>
          {t(resultKey(result))}
          {query.get('note') === 'old_link_may_remain' ? ` ${t('player.linkMayRemain')}` : ''}
        </Notice>
      ) : null}
      {note ? <Notice kind="success">{note}</Notice> : null}
      {action.error ? <Notice kind="error">{action.error}</Notice> : null}
      <p>
        {connected
          ? t('player.dialog.connected')
          : needsReconnect
            ? t('player.dialog.reconnect')
            : t('player.dialog.intro')}
      </p>
      <p className="flex flex-wrap gap-3">
        {status.linkEnabled ? (
          <Button disabled={action.busy} onClick={connect}>
            {connected || needsReconnect ? t('player.reconnect') : t('player.connect')}
          </Button>
        ) : null}
        {connected || needsReconnect ? (
          <Button
            variant="secondary"
            disabled={action.busy}
            onClick={() => {
              setConfirming(true)
            }}
          >
            {t('player.disconnect')}
          </Button>
        ) : null}
      </p>
      <UncertainPlayer
        keep={removal}
        version={version}
        onDecided={() => {
          setVersion((n) => n + 1)
        }}
        onChanged={() => {
          setVersion((n) => n + 1)
        }}
      />
      <ConfirmDisconnect
        open={confirming}
        title={t('player.disconnectTitle')}
        text={t('player.disconnectText')}
        yes={t('player.disconnectYes')}
        busy={action.busy}
        onYes={disconnect}
        onClose={() => {
          setConfirming(false)
        }}
      />
    </div>
  )
}
