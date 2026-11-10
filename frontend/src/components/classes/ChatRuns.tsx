import { useEffect, useRef, useState } from 'react'
import type { ClassMessage } from '../../api/classes'
import {
  retryLabel,
  reviseFailureText,
  songsOnlyChange,
  songsOnlyOwnPlaylist,
  songsOnlyPickPending,
  musicOnlyRunning,
  musicOnlySoundless,
  stageLabel,
} from '../../api/jobMessages'
import { cancelJob, getJob, isTerminal, type Job } from '../../api/jobs'
import { useT } from '../../i18n/I18n'
import { useJobProgress } from '../../hooks/useJobProgress'
import { Link } from '../../router/Router'
import { Notice, useAction } from '../forms'
import { CHANGE_SONGS_ID } from '../songs/ChangeSongs'
import { Button } from '../ui/button'
import { UncertainAttach } from './UncertainAttach'

/** Where the class's songs stand, for the answer to a request that only asks for other songs. */
export type SongsWay = 'change' | 'own' | 'pending' | 'running' | 'soundless'

/** The live progress of the class's running change, from the job's events. */
export function ReviseRun({ jobId, onFinished }: { jobId: string; onFinished: () => void }) {
  const t = useT()
  const { job, progress, problem, setJob } = useJobProgress(jobId)
  const action = useAction()
  const over = job !== null && isTerminal(job.state)
  const finished = useRef(false)
  useEffect(() => {
    if (over && !finished.current) {
      finished.current = true
      onFinished()
    }
  }, [over, onFinished])

  if (problem === 'not_found') return <Notice>{t('class.chat.noLonger')}</Notice>
  const stopping = job?.cancelRequested === true || progress.cancelRequested
  const stop = () => {
    void action.run(async () => {
      setJob(await cancelJob(jobId))
    })
  }
  return (
    <div className="chat__run" role="status" aria-live="polite" data-testid="revise-progress">
      {problem === 'failed' ? <p>{t('class.runDropped')}</p> : null}
      {stopping ? (
        <p>{t('class.runStopping')}</p>
      ) : progress.retry !== null ? (
        <p>{retryLabel(progress.retry)}</p>
      ) : job === null || (job.state === 'queued' && progress.stage === null) ? (
        <p>{t('class.runWaiting')}</p>
      ) : (
        <p>{stageLabel(progress.stage ?? job.stage)}…</p>
      )}
      {progress.operation === 'pending' ? <p>{t('class.chat.addingPlaylist')}</p> : null}
      {action.error ? <Notice kind="error">{action.error}</Notice> : null}
      <button
        type="button"
        className="button button--secondary"
        disabled={action.busy || stopping || over}
        onClick={stop}
      >
        {t('class.chat.stop')}
      </button>
    </div>
  )
}

/** What became of a request that got no answer: the job's failure, in plain words. */
export function Unanswered({
  message,
  latest,
  songs,
}: {
  message: ClassMessage
  latest: boolean
  /** Where the class's songs stand: Change songs is there, a playlist of the user's own, or a pick not done. */
  songs: SongsWay
}) {
  const t = useT()
  const canChangeSongs = songs === 'change'
  const [job, setJob] = useState<Job | null>(null)
  const [failed, setFailed] = useState(false)
  const [resolved, setResolved] = useState(false)
  const jobId = message.jobId
  useEffect(() => {
    if (!latest || jobId === null) return
    let current = true
    getJob(jobId).then(
      (found) => {
        if (current) setJob(found)
      },
      () => {
        if (current) setFailed(true)
      },
    )
    return () => {
      current = false
    }
  }, [latest, jobId])

  if (!latest || jobId === null || failed) {
    return <p className="chat__outcome muted">{t('class.chat.unfinished')}</p>
  }
  if (job === null) return <p className="chat__outcome muted">{t('class.chat.checking')}</p>
  if (job.state === 'failed' && job.errorCategory === 'uncertain_write') {
    return (
      <div className="chat__outcome">
        <UncertainAttach
          jobId={job.id}
          onResolved={() => {
            setResolved(true)
          }}
        />
        {resolved ? <p>{t('class.chat.askAgain')}</p> : null}
      </div>
    )
  }
  if (job.state === 'failed' && job.errorCategory === 'use_change_songs') {
    return (
      <div className="chat__outcome" role="status" data-testid="use-change-songs">
        <p>
          {songs === 'change'
            ? songsOnlyChange()
            : songs === 'soundless'
              ? musicOnlySoundless()
              : songs === 'running'
                ? musicOnlyRunning()
                : songs === 'own'
                  ? songsOnlyOwnPlaylist()
                  : songsOnlyPickPending()}
        </p>
        {canChangeSongs ? (
          <Button
            variant="secondary"
            onClick={() => {
              const panel = document.getElementById(CHANGE_SONGS_ID)
              panel?.scrollIntoView({ block: 'start' })
              panel?.focus()
            }}
          >
            {t('class.chat.changeSongs')}
          </Button>
        ) : null}
      </div>
    )
  }
  if (job.state === 'failed') {
    const sentence = reviseFailureText(job.errorCategory, songs === 'soundless')
    return (
      <p className="chat__outcome" role="alert">
        {sentence}{' '}
        {job.errorCategory === 'not_connected' ||
        job.errorCategory === 'needs_reconnect' ||
        job.errorCategory === 'key_unavailable' ||
        job.errorCategory === 'decrypt_failed' ? (
          <Link to="/account">{t('class.goToAccount')}</Link>
        ) : null}
      </p>
    )
  }
  if (job.state === 'cancelled' || job.state === 'expired') {
    return <p className="chat__outcome muted">{t('class.chat.stopped')}</p>
  }
  return <p className="chat__outcome muted">{t('class.chat.unfinished')}</p>
}
