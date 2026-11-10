import { useCallback, useState } from 'react'
import { planRefusalText } from '../../api/planMessages'
import { sentenceText } from '../../api/sentences'
import { useT } from '../../i18n/I18n'
import { t as translate } from '../../i18n/t'
import { failureText } from '../../api/jobMessages'
import { createDesignJob, type DesignInput, type Job } from '../../api/jobs'
import { ApiError } from '../../api/client'
import { Notice, useAction } from '../forms'
import { Button } from '../ui/button'
import { Card as Panel } from '../ui/card'
import { UncertainAttach } from '../classes/UncertainAttach'
import { startAgainPath } from '../../lib/startRequest'
import { designAgainKey, forgetInput, recallInput, rememberInput } from '../../lib/submittedInput'
import { Link, useRouter } from '../../router/Router'

/** The end of a design job that is not a song job: failed, stopped or expired, with Design again. */
/** Starts the same design again as a new job of the same class, from the remembered form. */
function useDesignAgain(job: Job) {
  const { navigate } = useRouter()
  const action = useAction()
  const input = recallInput(job.id)
  const again = () => {
    if (input === null) {
      navigate(startAgainPath(job.classId))
      return
    }
    void action.run(async () => {
      const next: DesignInput = {
        class_id: job.classId,
        brief: input.brief,
        music: input.music,
        mowl_connection_id: input.mowl_connection_id,
      }
      let created: Job
      try {
        created = await createDesignJob(next, designAgainKey(job.id))
      } catch (error) {
        // The old connection id is gone for good (MOWL was connected again): drop the remembered
        // form so the page offers the form, which picks the current connection.
        if (error instanceof ApiError && error.reason === 'not_connected') forgetInput(job.id)
        throw error
      }
      rememberInput(created.id, next)
      forgetInput(job.id)
      navigate(`/jobs/${created.id}`)
    }, againError)
  }
  return { again, action, remembered: input !== null }
}

/**
 * The words for a failed Design again. `not_connected` also happens when MOWL was disconnected
 * and connected again since: the remembered form names the old connection, which is gone for
 * good (see `useDesignAgain`, which then drops it).
 */
function againError(error: unknown): string {
  if (error instanceof ApiError && error.reason === 'not_connected') {
    return translate('job.again.notConnected')
  }
  return againMessage(error)
}

function againMessage(error: unknown): string {
  const refusal = planRefusalText(error)
  if (refusal !== null) return refusal
  if (error instanceof ApiError && error.reason === 'class_busy') {
    return translate('job.again.classBusy')
  }
  if (error instanceof ApiError && error.status === 429) {
    return translate('job.again.tooMany')
  }
  return translate('common.didNotWork')
}

export function Failed({ job }: { job: Job }) {
  const t = useT()
  if (job.errorCategory === 'uncertain_write') return <FailedUncertain job={job} />
  // A failed Swedebeat match: the server's sentence says what stopped it. The generic MOWL text for
  // the category ("wait a few minutes and try again") would promise a retry that does not happen.
  // No button either: "Try again" would start a new design, not retry the match.
  if (job.kind === 'swedebeat_match' && job.errorMessage !== null) {
    const detail = sentenceText(t, job.sentence, job.errorMessage) ?? job.errorMessage
    return <Ended job={job} title={t('job.matchStopped')} detail={detail} retry={false} />
  }
  const kept = recallInput(job.id)
  const feature =
    kept?.brief.sport === 'running'
      ? 'running'
      : kept?.music.source === 'mowl_swedebeat_mix'
        ? 'swedebeat'
        : kept?.music.source === 'none'
          ? 'soundless'
          : null
  const text = failureText(job.errorCategory, '', feature)
  return <Ended job={job} title={text.title} detail={text.detail} account={text.account === true} />
}

function FailedUncertain({ job }: { job: Job }) {
  const t = useT()
  const [resolved, setResolved] = useState(false)
  const onResolved = useCallback(() => {
    setResolved(true)
  }, [])
  const { again, action, remembered } = useDesignAgain(job)
  return (
    <div className="stack">
      <UncertainAttach jobId={job.id} onResolved={onResolved} />
      {action.error ? <Notice kind="error">{action.error}</Notice> : null}
      {resolved ? (
        <p>
          <button type="button" className="button" disabled={action.busy} onClick={again}>
            {remembered ? t('job.again.design') : t('job.again.startNew')}
          </button>
        </p>
      ) : null}
    </div>
  )
}

export function Ended({
  job,
  title,
  detail,
  account = false,
  retry = true,
}: {
  job: Job
  title: string
  detail: string
  account?: boolean
  retry?: boolean
}) {
  const t = useT()
  const { again, action, remembered } = useDesignAgain(job)
  return (
    <Panel className="mx-auto flex w-full max-w-2xl flex-col gap-4" role="region" aria-labelledby="ended-title">
      <h1 id="ended-title" className="m-0 font-display text-3xl font-extrabold">
        {title}
      </h1>
      <p className="m-0 text-muted">{detail}</p>
      {action.error ? <Notice kind="error">{action.error}</Notice> : null}
      <p className="m-0 flex flex-wrap gap-3">
        {account ? (
          <Button asChild>
            <Link to="/account">{t('job.goToAccount')}</Link>
          </Button>
        ) : null}
        {retry ? (
          <Button variant={account ? 'secondary' : 'primary'} disabled={action.busy} onClick={again}>
            {remembered
              ? job.state === 'failed'
                ? t('job.again.tryAgain')
                : t('job.again.design')
              : t('job.again.startNew')}
          </Button>
        ) : null}
      </p>
    </Panel>
  )
}
