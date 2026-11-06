import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { ApiError } from '../../api/client'
import { cancelJob, type Job } from '../../api/jobs'
import { approveRefusal, startError, type Refusal } from '../../api/songMessages'
import {
  approveSongs,
  createPickJob,
  getSongList,
  PICK_KIND,
  type PickInput,
  type SongList,
} from '../../api/songs'
import { useT } from '../../i18n/I18n'
import { t as translate } from '../../i18n/t'
import {
  currentConnectionIds,
  digest,
  classContext,
  followUpBrief,
  isNotConnected,
  minutes,
  startAfterCancel,
  whenText,
} from '../../lib/songs'
import { actionKey, dropActionKey, recallPick, rememberPick } from '../../lib/submittedInput'
import { Link, useRouter } from '../../router/Router'
import { Notice, useAction } from '../forms'
import { Button } from '../ui/button'
import { Card, CardTitle } from '../ui/card'
import { CoverMosaic } from './CoverArt'
import { SongRows } from './SongRows'
import { SpotifyAttribution } from './SpotifyAttribution'

type Load =
  | { status: 'loading' }
  | { status: 'failed' }
  | { status: 'ready'; list: SongList }

function useSongList(job: Job) {
  const [load, setLoad] = useState<Load>({ status: 'loading' })
  const [round, setRound] = useState(0)
  useEffect(() => {
    if (job.approvalRevisionId === null) return
    const abort = new AbortController()
    getSongList(job.classId, job.approvalRevisionId, abort.signal).then(
      (list) => {
        if (!abort.signal.aborted) setLoad({ status: 'ready', list })
      },
      () => {
        if (!abort.signal.aborted) setLoad({ status: 'failed' })
      },
    )
    return () => {
      abort.abort()
    }
  }, [job.classId, job.approvalRevisionId, round])
  const reload = useCallback(() => {
    setLoad({ status: 'loading' })
    setRound((n) => n + 1)
  }, [])
  // A job without an approval revision has no list to show.
  return { load: job.approvalRevisionId === null ? ({ status: 'failed' } as Load) : load, reload }
}

/**
 * Starts a new pick for the class of `job` after stopping the waiting one (ADR 0008 section 5):
 * the two calls are sequential, and a `class_busy` answer to the second waits for the first.
 */
function useNewPick(job: Job, list: SongList) {
  const { navigate } = useRouter()
  const action = useAction()
  const locked = useRef(false)
  const start = (replace: number[] | null, note: string) => {
    if (locked.current) return
    locked.current = true
    const scope = `pick.${job.id}.${digest(`${(replace ?? []).join(',')}|${note}`)}`
    void action
      .run(async () => {
        try {
          await cancelJob(job.id)
        } catch (error) {
          // 409: the job ended on its own (expired, say), which a new pick accepts as well.
          if (!(error instanceof ApiError && error.status === 409)) throw error
        }
        const ids = await currentConnectionIds()
        // The class's own brief; this tab's remembered form only when the class has none. The
        // waiting job is already stopped here, so a failed read must say so and not look like
        // nothing happened.
        const context = await classContext(job.classId).catch(() => {
          throw new StoppedError()
        })
        const kept = recallPick(job.id)
        const input: PickInput = {
          class_id: job.classId,
          brief: followUpBrief(context.brief ?? kept?.brief ?? null, list.targetSec, note),
          mode: 'pick',
          ...(replace !== null && replace.length > 0
            ? { seed: { song_list_revision_id: list.id, replace } }
            : {}),
          mowl_connection_id: ids.mowl,
          spotify_connection_id: ids.spotify,
        }
        const created = await startAfterCancel(() => createPickJob(input, actionKey(scope)))
        dropActionKey(scope)
        rememberPick(created.id, input)
        navigate(`/jobs/${created.id}`)
      }, describeStart)
      .finally(() => {
        locked.current = false
      })
  }
  return { start, action }
}

/** The waiting songs were stopped, but the class could not be read for the new pick. */
class StoppedError extends Error {}

function describeStart(error: unknown): string {
  if (error instanceof StoppedError) return translate('songs.approval.stopped')
  if (isNotConnected(error)) return startError(new ApiError(409, 'not_connected', null, undefined))
  if (error instanceof ApiError && error.reason === 'invalid_input') {
    return translate('songs.approval.cannotChange')
  }
  return startError(error)
}

export function SongsForApproval({
  job,
  onApproved,
}: {
  job: Job
  /** The approval was accepted: the job is queued again and runs its second part. */
  onApproved: (job: Job) => void
}) {
  const t = useT()
  const { load, reload } = useSongList(job)
  if (load.status === 'loading') {
    return (
      <Card>
        <p className="m-0">{t('songs.approval.loading')}</p>
      </Card>
    )
  }
  if (load.status === 'failed') {
    return (
      <Card>
        <CardTitle>{t('songs.approval.loadFailedTitle')}</CardTitle>
        <Notice kind="error">{t('songs.approval.loadFailed')}</Notice>
        <Button onClick={reload}>{t('common.tryAgain')}</Button>
      </Card>
    )
  }
  return <Approval job={job} list={load.list} onApproved={onApproved} reload={reload} />
}

function Approval({
  job,
  list,
  onApproved,
  reload,
}: {
  job: Job
  list: SongList
  onApproved: (job: Job) => void
  reload: () => void
}) {
  const t = useT()
  const [refusal, setRefusal] = useState<Refusal | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [marks, setMarks] = useState<ReadonlySet<number>>(new Set())
  const [note, setNote] = useState('')
  const locked = useRef(false)
  const pick = useNewPick(job, list)
  const isPick = job.kind === PICK_KIND

  const toggle = (position: number) => {
    setMarks((previous) => {
      const next = new Set(previous)
      if (!next.delete(position)) next.add(position)
      return next
    })
  }

  const approve = () => {
    // One click is one request: a second click before the answer is ignored.
    if (locked.current) return
    locked.current = true
    setBusy(true)
    setProblem(null)
    approveSongs(job.id, list.id, list.contentHash).then(
      (next) => {
        setBusy(false)
        locked.current = false
        onApproved(next)
      },
      (error: unknown) => {
        const found = approveRefusal(error)
        // A refused approval is shown as it is: a superseded or expired list is never tried again.
        setRefusal(found)
        setProblem(found.text)
        setBusy(false)
        locked.current = false
      },
    )
  }

  if (list.status === 'approved') {
    return (
      <Card>
        <CardTitle>{t('songs.approval.approvedTitle')}</CardTitle>
        <p className="m-0">{t('songs.approval.approvedText')}</p>
        <Button onClick={reload}>{t('songs.approval.refresh')}</Button>
      </Card>
    )
  }
  if (list.status === 'superseded') {
    return (
      <Ended title={t('songs.approval.supersededTitle')} classId={job.classId}>
        {t('songs.approval.supersededText')}
      </Ended>
    )
  }
  if (list.status === 'expired' || list.status === 'cancelled') {
    const expired = list.status === 'expired'
    return (
      <Ended
        title={expired ? t('songs.approval.expiredTitle') : t('songs.approval.stoppedTitle')}
        classId={job.classId}
        error={pick.action.error}
        extra={isPick ? <PickAgain pick={pick} /> : null}
      >
        {expired ? t('songs.approval.expiredText') : t('songs.approval.stoppedText')}
      </Ended>
    )
  }

  const final = refusal?.final === true
  const replace = [...marks].sort((a, b) => a - b).map((position) => position - 1)
  return (
    <section className="flex flex-col gap-4" aria-labelledby="songs-title">
      <header className="flex flex-col items-start gap-4 rounded-lg border border-line bg-surface p-4 sm:flex-row sm:items-end sm:p-6">
        <CoverMosaic songs={list.songs} className="size-40 shrink-0 sm:size-48" />
        <div className="flex min-w-0 flex-col gap-2">
          <p className="m-0 text-[0.8125rem] font-medium uppercase tracking-wide text-muted">{t('songs.approval.eyebrow')}</p>
          <h1 id="songs-title" className="m-0 break-words font-display text-3xl font-extrabold text-text sm:text-4xl">
            {t('songs.approval.heading')}
          </h1>
          <p className="m-0 text-text">
            {t('songs.approval.summary', {
              count: list.songs.length,
              total: minutes(list.totalSec),
              target: minutes(list.targetSec),
            })}
          </p>
          <SpotifyAttribution />
        </div>
      </header>
      {list.note !== '' ? <p className="m-0 text-muted">{list.note}</p> : null}
      {list.omittedNoBpm > 0 ? (
        <Notice>
          {t('songs.approval.omittedNoBpm', { count: list.omittedNoBpm })}
        </Notice>
      ) : null}
      <SongRows
        songs={list.songs}
        marks={isPick && !final ? marks : undefined}
        onToggle={isPick && !final ? toggle : undefined}
        disabled={busy || pick.action.busy}
      />
      <p className="m-0 text-[0.9375rem] text-muted">{t('songs.approval.privacy')}</p>
      {list.expiresAt !== null ? (
        <p className="m-0 text-[0.9375rem] text-muted" data-testid="expiry">
          {t('songs.approval.expiry', { when: whenText(list.expiresAt) })}
        </p>
      ) : null}
      {problem !== null ? <Notice kind="error">{problem}</Notice> : null}
      {pick.action.error ? <Notice kind="error">{pick.action.error}</Notice> : null}
      {refusal?.account === true ? (
        <p className="m-0">
          <Link to="/account">{t('job.goToAccount')}</Link>
        </p>
      ) : null}
      {final ? (
        <FinalActions refusal={refusal} classId={job.classId} pick={isPick ? pick : null} reload={reload} />
      ) : (
        <>
          <div className="flex flex-wrap gap-3">
            <Button disabled={busy || pick.action.busy} onClick={approve}>
              {t('songs.approval.approve')}
            </Button>
          </div>
          {isPick ? (
            <div className="flex flex-col gap-2">
              <label htmlFor="change-note" className="text-[0.9375rem] font-medium text-text">
                {t('songs.approval.noteLabel')}
              </label>
              <textarea
                id="change-note"
                className="min-h-20 rounded-md border border-line bg-raised p-3 text-text"
                maxLength={500}
                value={note}
                onChange={(e) => {
                  setNote(e.target.value)
                }}
              />
              <p className="m-0 text-[0.9375rem] text-muted">
                {t('songs.approval.markHint')}
              </p>
              <div>
                <Button
                  variant="secondary"
                  disabled={busy || pick.action.busy || replace.length === 0}
                  onClick={() => {
                    pick.start(replace, note)
                  }}
                >
                  {t('songs.approval.askChanges')}
                </Button>
              </div>
            </div>
          ) : (
            <div>
              <Button
                variant="secondary"
                disabled={busy || pick.action.busy}
                onClick={() => {
                  pick.start(null, '')
                }}
              >
                {t('songs.approval.stopAndChange')}
              </Button>
            </div>
          )}
        </>
      )}
    </section>
  )
}

function FinalActions({
  refusal,
  classId,
  pick,
  reload,
}: {
  refusal: Refusal
  classId: string
  pick: ReturnType<typeof useNewPick> | null
  reload: () => void
}) {
  const t = useT()
  return (
    <div className="flex flex-wrap gap-3">
      {refusal.newSongs === true && pick !== null ? <PickAgain pick={pick} /> : null}
      <Button variant="secondary" onClick={reload}>
        {t('songs.approval.reload')}
      </Button>
      <Button asChild variant="ghost">
        <Link to={`/classes/${classId}`}>{t('songs.approval.openClass')}</Link>
      </Button>
    </div>
  )
}

function PickAgain({ pick }: { pick: ReturnType<typeof useNewPick> }) {
  const t = useT()
  return (
    <Button
      disabled={pick.action.busy}
      onClick={() => {
        pick.start(null, '')
      }}
    >
      {t('songs.ended.pickAgain')}
    </Button>
  )
}

function Ended({
  title,
  classId,
  children,
  extra = null,
  error = null,
}: {
  title: string
  classId: string
  children: ReactNode
  extra?: ReactNode
  error?: string | null
}) {
  const t = useT()
  return (
    <Card>
      <CardTitle>{title}</CardTitle>
      <div className="flex flex-col gap-3">
        <p className="m-0 text-text">{children}</p>
        {error ? <Notice kind="error">{error}</Notice> : null}
        <div className="flex flex-wrap gap-3">
          {extra}
          <Button asChild variant="ghost">
            <Link to={`/classes/${classId}`}>{t('songs.approval.openClass')}</Link>
          </Button>
        </div>
      </div>
    </Card>
  )
}
