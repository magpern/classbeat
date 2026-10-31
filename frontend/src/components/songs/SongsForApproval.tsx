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

export const PRIVACY_TEXT =
  'When you approve, classbeat creates a private playlist with these songs in your own Spotify account. A private playlist does not appear on your profile or in search, but anyone with its link can open it.'

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

export const STOPPED_TEXT =
  'These songs were stopped, but classbeat could not read the class, so nothing new was started. Press Ask for changes again to try once more.'

function describeStart(error: unknown): string {
  if (error instanceof StoppedError) return STOPPED_TEXT
  if (isNotConnected(error)) return startError(new ApiError(409, 'not_connected', null, undefined))
  if (error instanceof ApiError && error.reason === 'invalid_input') {
    return 'These songs can no longer be changed, so nothing was started. Pick songs again.'
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
  const { load, reload } = useSongList(job)
  if (load.status === 'loading') {
    return (
      <Card>
        <p className="m-0">Loading the songs</p>
      </Card>
    )
  }
  if (load.status === 'failed') {
    return (
      <Card>
        <CardTitle>The songs could not be loaded</CardTitle>
        <Notice kind="error">The songs could not be loaded. Nothing was approved.</Notice>
        <Button onClick={reload}>Try again</Button>
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
        <CardTitle>You approved these songs</CardTitle>
        <p className="m-0">classbeat is making your playlist and finishing the class.</p>
        <Button onClick={reload}>Refresh</Button>
      </Card>
    )
  }
  if (list.status === 'superseded') {
    return (
      <Ended title="A newer list replaced these songs" classId={job.classId}>
        A newer list of songs has replaced these, so they cannot be approved any more.
      </Ended>
    )
  }
  if (list.status === 'expired' || list.status === 'cancelled') {
    const expired = list.status === 'expired'
    return (
      <Ended
        title={expired ? 'These songs expired' : 'These songs were stopped'}
        classId={job.classId}
        error={pick.action.error}
        extra={isPick ? <PickAgain pick={pick} /> : null}
      >
        {expired
          ? 'These songs expired before they were approved. Nothing was written to your Spotify.'
          : 'These songs were stopped. Nothing was written to your Spotify.'}
      </Ended>
    )
  }

  const final = refusal?.final === true
  const replace = [...marks].sort((a, b) => a - b).map((position) => position - 1)
  return (
    <section className="flex flex-col gap-4" aria-labelledby="songs-title">
      <header className="flex flex-wrap items-end gap-4">
        <CoverMosaic songs={list.songs} />
        <div className="flex min-w-0 flex-col gap-2">
          <h1 id="songs-title" className="m-0 font-display text-3xl font-bold text-text">
            Songs for your class
          </h1>
          <p className="m-0 text-text">
            {list.songs.length} songs, {minutes(list.totalSec)} in total. You asked for{' '}
            {minutes(list.targetSec)}.
          </p>
        </div>
      </header>
      {list.note !== '' ? <p className="m-0 text-muted">{list.note}</p> : null}
      {list.omittedNoBpm > 0 ? (
        <Notice>
          {list.omittedNoBpm} song{list.omittedNoBpm === 1 ? ' was' : 's were'} left out because MOWL
          has no BPM for {list.omittedNoBpm === 1 ? 'it' : 'them'}.
        </Notice>
      ) : null}
      <SongRows
        songs={list.songs}
        marks={isPick && !final ? marks : undefined}
        onToggle={isPick && !final ? toggle : undefined}
        disabled={busy || pick.action.busy}
      />
      <p className="m-0 text-[0.9375rem] text-muted">{PRIVACY_TEXT}</p>
      {list.expiresAt !== null ? (
        <p className="m-0 text-[0.9375rem] text-muted" data-testid="expiry">
          Approve by {whenText(list.expiresAt)}. After that these songs expire and nothing is
          written to your Spotify.
        </p>
      ) : null}
      {problem !== null ? <Notice kind="error">{problem}</Notice> : null}
      {pick.action.error ? <Notice kind="error">{pick.action.error}</Notice> : null}
      {refusal?.account === true ? (
        <p className="m-0">
          <Link to="/account">Go to your account</Link>
        </p>
      ) : null}
      {final ? (
        <FinalActions refusal={refusal} classId={job.classId} pick={isPick ? pick : null} reload={reload} />
      ) : (
        <>
          <div className="flex flex-wrap gap-3">
            <Button disabled={busy || pick.action.busy} onClick={approve}>
              Approve songs
            </Button>
          </div>
          {isPick ? (
            <div className="flex flex-col gap-2">
              <label htmlFor="change-note" className="text-[0.9375rem] font-medium text-text">
                What should be different? (optional)
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
                Mark the songs to replace, then ask for changes. The other songs stay.
              </p>
              <div>
                <Button
                  variant="secondary"
                  disabled={busy || pick.action.busy || replace.length === 0}
                  onClick={() => {
                    pick.start(replace, note)
                  }}
                >
                  Ask for changes
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
                Stop and change the request
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
  return (
    <div className="flex flex-wrap gap-3">
      {refusal.newSongs === true && pick !== null ? <PickAgain pick={pick} /> : null}
      <Button variant="secondary" onClick={reload}>
        Reload the page
      </Button>
      <Button asChild variant="ghost">
        <Link to={`/classes/${classId}`}>Open the class</Link>
      </Button>
    </div>
  )
}

function PickAgain({ pick }: { pick: ReturnType<typeof useNewPick> }) {
  return (
    <Button
      disabled={pick.action.busy}
      onClick={() => {
        pick.start(null, '')
      }}
    >
      Pick songs again
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
  return (
    <Card>
      <CardTitle>{title}</CardTitle>
      <div className="flex flex-col gap-3">
        <p className="m-0 text-text">{children}</p>
        {error ? <Notice kind="error">{error}</Notice> : null}
        <div className="flex flex-wrap gap-3">
          {extra}
          <Button asChild variant="ghost">
            <Link to={`/classes/${classId}`}>Open the class</Link>
          </Button>
        </div>
      </div>
    </Card>
  )
}
