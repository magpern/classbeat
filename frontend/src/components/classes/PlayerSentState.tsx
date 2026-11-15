import type { ClassSummary } from '../../api/classes'
import { useT } from '../../i18n/I18n'

/**
 * What the player holds of this class (ADR 0034 section 6, #1068), from the class answer's `player`
 * field, so it is the same after a reload: not sent, sent (version N), or sent while the current
 * version is newer. Loaded only for a user who has the player.
 */
export default function PlayerSentState({ summary }: { summary: ClassSummary }) {
  const t = useT()
  const sent = summary.player
  const current = summary.currentRevisionNumber
  let line = t('player.state.notSent')
  if (sent !== null) {
    const number = String(sent.sentRevisionNumber)
    line =
      current !== null && current > sent.sentRevisionNumber
        ? t('player.state.newer', { number, current: String(current) })
        : t('player.state.sent', { number })
  }
  return (
    <p className="m-0 text-sm text-muted" data-testid="player-state">
      {line}
    </p>
  )
}
