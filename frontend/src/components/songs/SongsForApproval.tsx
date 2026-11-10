import { useEffect, useRef, useState } from 'react'
import { type Job } from '../../api/jobs'
import { approveRefusal, type Refusal } from '../../api/songMessages'
import { approveSongs, PICK_KIND, type SongList } from '../../api/songs'
import { useT } from '../../i18n/I18n'
import { minutes, whenText } from '../../lib/songs'
import { Link } from '../../router/Router'
import { Notice } from '../forms'
import { Button } from '../ui/button'
import { Card, CardTitle } from '../ui/card'
import { Ended, FinalActions, Heading, PickAgain, RefreshButton } from './ApprovalEnded'
import { StickyActionBar } from '../ui/sticky-action-bar'
import { CoverMosaic } from './CoverArt'
import { SongRows } from './SongRows'
import { SpotifyAttribution } from './SpotifyAttribution'
import { useNewPick } from './useNewPick'
import { useSongList } from './useSongList'

/** In the class workspace the list is one column under the page's own heading, and a change request stays on the class. */
export interface EmbeddedApproval {
  /** The class changed (a new pick was started, a list was replaced): read it again. */
  onChanged: () => void
}

export function SongsForApproval({
  job,
  onApproved,
  embedded,
}: {
  job: Job
  /** The approval was accepted: the job is queued again and runs its second part. */
  onApproved: (job: Job) => void
  embedded?: EmbeddedApproval
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
  return <Approval job={job} list={load.list} onApproved={onApproved} reload={reload} embedded={embedded} />
}

function Approval({
  job,
  list,
  onApproved,
  reload,
  embedded,
}: {
  job: Job
  list: SongList
  onApproved: (job: Job) => void
  reload: () => void
  embedded: EmbeddedApproval | undefined
}) {
  const t = useT()
  const [refusal, setRefusal] = useState<Refusal | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [marks, setMarks] = useState<ReadonlySet<number>>(new Set())
  const [note, setNote] = useState('')
  const locked = useRef(false)
  // The notices sit at the top of a long page, the button in the bar: bring a new one into view.
  const notices = useRef<HTMLDivElement>(null)
  const pick = useNewPick(job, list, embedded?.onChanged)
  // The class page is the place itself: no link back to it, a refresh instead.
  const classId = embedded === undefined ? job.classId : null
  const isPick = job.kind === PICK_KIND
  const shown = problem ?? pick.action.error
  useEffect(() => {
    if (shown !== null) notices.current?.scrollIntoView({ block: 'nearest' })
  }, [shown])

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
      <Ended
        title={t('songs.approval.supersededTitle')}
        classId={classId}
        extra={embedded ? <RefreshButton onClick={embedded.onChanged} /> : null}
      >
        {t('songs.approval.supersededText')}
      </Ended>
    )
  }
  if (list.status === 'expired' || list.status === 'cancelled') {
    const expired = list.status === 'expired'
    return (
      <Ended
        title={expired ? t('songs.approval.expiredTitle') : t('songs.approval.stoppedTitle')}
        classId={classId}
        error={pick.action.error}
        extra={isPick ? <PickAgain pick={pick} /> : null}
      >
        {expired ? t('songs.approval.expiredText') : t('songs.approval.stoppedText')}
      </Ended>
    )
  }

  const final = refusal?.final === true
  const replace = [...marks].sort((a, b) => a - b).map((position) => position - 1)
  const busyNow = busy || pick.action.busy
  const expiry =
    list.expiresAt !== null ? (
      <p className="m-0 min-w-0 text-[0.8125rem] text-muted max-lg:flex-1" data-testid="expiry">
        {t('songs.approval.expiry', { when: whenText(list.expiresAt) })}
      </p>
    ) : null
  return (
    <section
      className={embedded ? 'flex flex-col gap-5' : 'grid items-start gap-5 lg:grid-cols-[minmax(0,1fr)_20rem]'}
      aria-labelledby="songs-title"
      data-testid={embedded ? 'inline-songs' : undefined}
    >
      <div className="flex min-w-0 flex-col gap-4">
        <div ref={notices} className="flex flex-col gap-4 empty:hidden">
          {problem !== null ? <Notice kind="error">{problem}</Notice> : null}
          {pick.action.error ? <Notice kind="error">{pick.action.error}</Notice> : null}
          {refusal?.account === true ? (
            <p className="m-0">
              <Link to="/account">{t('job.goToAccount')}</Link>
            </p>
          ) : null}
        </div>
        <header className="flex flex-col items-start gap-4 rounded-lg border border-line bg-surface p-4 sm:flex-row sm:items-end sm:p-6">
          <CoverMosaic songs={list.songs} className={embedded ? 'size-28 shrink-0' : 'size-40 shrink-0 sm:size-48'} />
          <div className="flex min-w-0 flex-col gap-2">
            {/* The workspace page has its own h1 (the class title). */}
            <Heading
              id="songs-title"
              as={embedded ? 'h2' : 'h1'}
              className={`m-0 break-words font-display font-extrabold text-text ${embedded ? 'text-2xl' : 'text-3xl sm:text-4xl'}`}
            >
              {t('songs.approval.heading')}
            </Heading>
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
          <Notice>{t('songs.approval.omittedNoBpm', { count: list.omittedNoBpm })}</Notice>
        ) : null}
        <SongRows
          songs={list.songs}
          marks={isPick && !final ? marks : undefined}
          onToggle={isPick && !final ? toggle : undefined}
          disabled={busyNow}
        />
        <p className="m-0 text-[0.9375rem] text-muted">{t('songs.approval.privacy')}</p>
        {final ? (
          <FinalActions refusal={refusal} classId={classId} pick={isPick ? pick : null} reload={reload} />
        ) : isPick ? (
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
            <p className="m-0 text-[0.9375rem] text-muted">{t('songs.approval.markHint')}</p>
            <div>
              <Button
                variant="secondary"
                disabled={busyNow || replace.length === 0}
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
              disabled={busyNow}
              onClick={() => {
                pick.start(null, '')
              }}
            >
              {t('songs.approval.stopAndChange')}
            </Button>
          </div>
        )}
      </div>
      {final ? null : embedded ? (
        // Not sticky: the workspace has its own bottom bar for saving on a phone.
        <div className="flex flex-wrap items-center gap-3" role="group" aria-label={t('songs.approval.barLabel')}>
          <Button disabled={busyNow} onClick={approve}>
            {t('songs.approval.approve')}
          </Button>
          {expiry}
        </div>
      ) : (
        <div className="min-w-0 lg:self-stretch">
          <StickyActionBar label={t('songs.approval.barLabel')} className="lg:p-5">
            {expiry}
            <Button disabled={busyNow} onClick={approve} className="w-full max-lg:w-auto max-lg:shrink-0">
              {t('songs.approval.approve')}
            </Button>
          </StickyActionBar>
        </div>
      )}
    </section>
  )
}
