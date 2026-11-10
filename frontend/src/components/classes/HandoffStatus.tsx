import type { Handoff } from '../../hooks/useHandoff'
import { interpretAgain } from '../../api/interpret'
import { useT } from '../../i18n/I18n'
import { recallRequest, rememberRequest, startAgainPath } from '../../lib/startRequest'
import { Link, useRouter } from '../../router/Router'
import { Notice, useAction } from '../forms'
import { Button } from '../ui/button'

/**
 * What the hand-off of a free-text class says in the class panel (MA3; the hand-off itself is
 * `useHandoff`): reading the request, starting the design, or why it stopped. A stored
 * `not_connected` refusal offers "Interpret again" (no credit: it reads the same request again in a
 * new class with the connections as they are now); any other refusal or failure offers the start
 * screen with the same request.
 */
export function HandoffStatus({ classId, handoff }: { classId: string; handoff: Handoff }) {
  const t = useT()
  const { navigate } = useRouter()
  const again = useAction()
  const { phase, refusal, retry } = handoff
  const sad = phase === 'refused' || phase === 'failed' || phase === 'error'
  if (phase === 'idle') return null
  if (!sad) {
    return <Notice>{phase === 'reading' ? t('start.handoff.waiting') : t('start.handoff.starting')}</Notice>
  }
  const interpretAgainNow = () => {
    void again
      .run(async () => {
        const made = await interpretAgain(classId)
        const kept = recallRequest(classId)
        if (kept !== null) rememberRequest(made.classId, kept)
        return made.classId
      })
      .then((id) => {
        if (id !== undefined) navigate(`/classes/${id}`)
      })
  }
  const notConnected = phase === 'refused' && refusal === 'not_connected'
  return (
    <div className="flex flex-col gap-3" data-testid="handoff-stopped">
      <Notice kind="error">
        {notConnected
          ? t('ws.refused.notConnected')
          : phase === 'refused'
            ? t('start.handoff.refused')
            : phase === 'failed'
              ? t('start.handoff.failed')
              : t('start.handoff.error')}
      </Notice>
      {again.error ? <Notice kind="error">{again.error}</Notice> : null}
      <p className="m-0 flex flex-wrap items-center gap-3">
        {notConnected ? (
          <>
            <Button disabled={again.busy} onClick={interpretAgainNow}>
              {t('ws.interpretAgain')}
            </Button>
            <Link to="/account">{t('class.goToAccount')}</Link>
          </>
        ) : phase === 'error' ? (
          <Button onClick={retry}>{t('ws.tryAgainNow')}</Button>
        ) : null}
        {notConnected ? null : <Link to={startAgainPath(classId)}>{t('start.handoff.tryAgain')}</Link>}
      </p>
    </div>
  )
}
