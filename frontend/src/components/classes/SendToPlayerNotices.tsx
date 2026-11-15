import { useEffect, useRef } from 'react'
import { useT } from '../../i18n/I18n'
import { Notice } from '../forms'
import { PlayerOutcome } from './PlayerOutcome'
import { PlayerRun } from './PlayerRun'
import { ConfirmButtons } from './SaveProgress'
export { default as PlayerSentState } from './PlayerSentState'
import type { PlayerFlow } from './useSendToPlayer'

/** The confirmation, the progress and the outcome of a send, under the header. */
export default function SendToPlayerNotices({ flow }: { flow: PlayerFlow }) {
  const t = useT()
  const { run, ended, action } = flow
  const confirmRef = useRef<HTMLDivElement>(null)
  // The confirmation takes focus when it opens, so a keyboard user lands on its buttons.
  useEffect(() => {
    if (flow.confirming) confirmRef.current?.focus()
  }, [flow.confirming])
  const close = () => {
    flow.setConfirming(false)
  }
  return (
    <>
      {ended ? <PlayerOutcome ended={ended} onSendAgain={() => { flow.setConfirming(true) }} /> : null}
      {action.error ? <Notice kind="error">{action.error}</Notice> : null}
      {flow.lost ? <Notice>{t('player.send.lost')}</Notice> : null}
      {run ? (
        <PlayerRun
          key={run.id}
          jobId={run.id}
          number={run.number}
          onLost={flow.onRunLost}
          onFinished={flow.onRunFinished}
        />
      ) : null}
      {flow.confirming ? (
        <div className="notice" role="group" aria-label={t('player.send.group')} tabIndex={-1} ref={confirmRef}>
          <p>{t('player.send.confirm', { number: String(flow.number ?? '?') })}</p>
          <ConfirmButtons
            label={t('player.send.button')}
            busy={action.busy}
            onConfirm={flow.press}
            onCancel={close}
          />
        </div>
      ) : null}
    </>
  )
}
