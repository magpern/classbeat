import { PLAYER_PATH } from '../../api/player'
import { useT } from '../../i18n/I18n'
import { Link } from '../../router/Router'
import { Button } from '../ui/button'
import type { PlayerFlow } from './useSendToPlayer'

/**
 * "Send to player" in the class header, beside Save to MOWL (ADR 0034 section 6). Connected: the
 * button, which only opens the confirmation. Not connected or needing a reconnect: a link to the
 * account page instead. Switched off and not connected: nothing.
 */
export function SendToPlayerActions({ flow }: { flow: PlayerFlow }) {
  const t = useT()
  if (!flow.offered || flow.confirming) return null
  const status = flow.player?.status
  if (status === 'not_connected' || status === 'needs_reconnect') {
    return (
      <Link className="button button--secondary" to={PLAYER_PATH}>
        {t(status === 'not_connected' ? 'player.send.connectFirst' : 'player.send.reconnectFirst')}
      </Link>
    )
  }
  return (
    <Button
      variant="secondary"
      disabled={!flow.canSend}
      onClick={() => {
        flow.setConfirming(true)
      }}
    >
      {flow.ended?.job.state === 'completed' ? t('player.send.again') : t('player.send.button')}
    </Button>
  )
}
