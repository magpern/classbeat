import type { Handoff } from '../../hooks/useHandoff'
import { ApiError } from '../../api/client'
import { designFromInterpretation, interpretAgain } from '../../api/interpret'
import { useT } from '../../i18n/I18n'
import { recallRequest, rememberRequest, startAgainPath } from '../../lib/startRequest'
import { Link, useRouter } from '../../router/Router'
import { Notice, useAction } from '../forms'
import { Button } from '../ui/button'
import { refusalCopy, type RefusalAction } from '../../lib/handoffRefusal'
import { PlanChoice } from '../plans/PlanChoice'

/**
 * What the hand-off of a free-text class says in the class panel (MA3; the hand-off itself is
 * `useHandoff`): reading the request, starting the design, or why it stopped. A stored
 * `not_connected` refusal offers "Interpret again" (no credit: it reads the same request again in a
 * new class with the connections as they are now). Every other stored reason has its own line and
 * actions (`lib/handoffRefusal`); a failure offers the start screen with the same request.
 */
export function HandoffStatus({ classId, handoff }: { classId: string; handoff: Handoff }) {
  const t = useT()
  const { navigate } = useRouter()
  const again = useAction()
  const retrying = useAction()
  const { phase, refusal, retry } = handoff
  const sad = phase === 'refused' || phase === 'failed' || phase === 'error'
  if (phase === 'idle') return null
  if (phase === 'choose' && handoff.held !== null) return <PlanChoice need={handoff.held} onWithin={handoff.designWithinPlan} />
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
  const retryRefusedNow = () => {
    // The stored refusal is answered as it is until a retry clears it (ADR 0038 amendment 2026-11-24):
    // ask the server once more, then read the hand-off again, whichever way it answered.
    void retrying
      .run(async () => {
        try {
          await designFromInterpretation(classId, true)
        } catch (error) {
          // Whatever the server answered (409 "already designed", 429 busy ...), the stored refusal
          // may have changed: read the hand-off again. Anything that is not an API answer is a real error.
          if (!(error instanceof ApiError)) throw error
        }
        return true
      })
      .then((done) => {
        if (done !== undefined) retry()
      })
  }
  const copy = phase === 'refused' ? refusalCopy(refusal) : null
  const message = copy !== null ? t(copy.text) : phase === 'failed' ? t('start.handoff.failed') : t('start.handoff.error')
  const actions: readonly RefusalAction[] = copy !== null ? copy.actions : phase === 'error' ? ['retry', 'startAgain'] : ['startAgain']
  const act = {
    interpretAgain: (
      <Button key="interpretAgain" disabled={again.busy} onClick={interpretAgainNow}>
        {t('ws.interpretAgain')}
      </Button>
    ),
    // After an error the hand-off itself is read again; after a refusal the server is asked again.
    retry: (
      <Button key="retry" disabled={retrying.busy} onClick={phase === 'refused' ? retryRefusedNow : retry}>
        {t('ws.tryAgainNow')}
      </Button>
    ),
    account: <Link key="account" to="/account">{t('class.goToAccount')}</Link>,
    spotify: <Link key="spotify" to="/account/spotify">{t('music.connectSpotify')}</Link>,
    startAgain: <Link key="startAgain" to={startAgainPath(classId)}>{t('start.handoff.tryAgain')}</Link>,
    classes: <Link key="classes" to="/classes">{t('class.list.title')}</Link>,
  }
  return (
    <div className="flex flex-col gap-3" data-testid="handoff-stopped" data-kind={copy?.kind ?? 'other'}>
      <Notice kind="error">{message}</Notice>
      {again.error ? <Notice kind="error">{again.error}</Notice> : null}
      {retrying.error ? <Notice kind="error">{retrying.error}</Notice> : null}
      {actions.length > 0 ? <p className="m-0 flex flex-wrap items-center gap-3">{actions.map((a) => act[a])}</p> : null}
    </div>
  )
}
