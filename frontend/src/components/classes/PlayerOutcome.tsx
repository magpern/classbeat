import { playerFailureText, playerNeedsReconnect } from '../../api/playerMessages'
import { PLAYER_PATH } from '../../api/player'
import { useT } from '../../i18n/I18n'
import { Link } from '../../router/Router'
import { Notice } from '../forms'
import { Button } from '../ui/button'
import { Cheer } from '../ui/cheer'
import type { SendEnded } from './useSendToPlayer'

/**
 * How a send ended (ADR 0034 section 6). Succeeded; the player was ahead or changed; the link needs a
 * reconnect; undecided (the panel below decides); failed; stopped. `onSendAgain` opens the same
 * confirmation as the first press: nothing is sent again by itself.
 */
export function PlayerOutcome({ ended, onSendAgain }: { ended: SendEnded; onSendAgain: () => void }) {
  const t = useT()
  const { job, number } = ended
  if (job.state === 'completed') {
    return (
      <Cheer>
        <Notice kind="success">{t('player.send.sent', { number: String(number) })}</Notice>
      </Cheer>
    )
  }
  if (job.state !== 'failed') return <Notice>{t('player.send.stopped')}</Notice>
  const category = job.errorCategory
  if (category === 'uncertain_write') return <Notice kind="error">{t('player.send.uncertain')}</Notice>
  if (playerNeedsReconnect(category)) {
    return (
      <Notice kind="error">
        {t('player.send.reconnectNote')} <Link to={PLAYER_PATH}>{t('player.send.reconnectLink')}</Link>
      </Notice>
    )
  }
  const again = category === 'player_ahead' || category === 'player_conflict'
  return (
    <div className="flex flex-col gap-3">
      <Notice kind="error">{playerFailureText(category)}</Notice>
      {again ? (
        <p>
          <Button variant="secondary" onClick={onSendAgain}>
            {t('player.send.again')}
          </Button>
        </p>
      ) : null}
    </div>
  )
}
