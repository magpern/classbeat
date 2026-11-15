import { useEffect, useState } from 'react'
import { cancelJob, type Job } from '../../api/jobs'
import {
  actOnStudioList,
  getStudioList,
  refusalChanges,
  studioError,
  type StudioAction,
  type StudioChanges,
  type StudioList,
} from '../../api/studioSongs'
import { useT } from '../../i18n/I18n'
import { minutes } from '../../lib/songs'
import { startAgainPath } from '../../lib/startRequest'
import { actionKey, dropActionKey } from '../../lib/submittedInput'
import { Link, useRouter } from '../../router/Router'
import { Notice, useAction } from '../forms'
import { Button } from '../ui/button'
import { Card, CardTitle } from '../ui/card'
import { StudioSongRow } from './StudioSongRow'
import { rememberStudioAction } from './studioAction'

// The list can be changed while it waits for approval, or after it ended without one (the server
// cancels a waiting job in the same step as the action).
const CHANGEABLE: readonly string[] = ['waiting_for_approval', 'cancelled', 'expired']

/**
 * The studio's song list (ADR 0036 sections 3 and 5): SwedeBeat's duration and BPM per song, gaps,
 * every swap as original → replacement with its reason, Undo and Ask for another per swap, the
 * class's song changes against its limit, and for a suspected outage "Try again later" or "Swap
 * anyway". `job` is the `studio_songs` job that made the list.
 *
 * TODO(#1097, C6a): the timeline, the output choice, the consent and the per-output states, and
 * the approval itself (then the `onApproved` seam), come here once the timeline's API exists. Nothing of them is faked.
 */
export function StudioSongs({
  job,
}: {
  job: Job
  /**
   * SEAM (part 2): called with the approved list's revision id once the list is approved. It is
   * where `studio_design` (C5, started after the approval) is created; its route is not here
   * because it is not merged yet, and no URL is invented.
   */
  onApproved?: (revisionId: string) => void
}) {
  const t = useT()
  const { navigate } = useRouter()
  const revisionId = job.approvalRevisionId
  const [list, setList] = useState<StudioList | null>(null)
  const [loadFailed, setLoadFailed] = useState(false)
  const [stopped, setStopped] = useState(false)
  const [refused, setRefused] = useState<StudioChanges | null>(null)
  const act = useAction()
  const stop = useAction()

  useEffect(() => {
    if (revisionId === null) return
    const abort = new AbortController()
    getStudioList(job.classId, revisionId, abort.signal).then(
      (found) => {
        if (!abort.signal.aborted) setList(found)
      },
      () => {
        if (!abort.signal.aborted) setLoadFailed(true)
      },
    )
    return () => {
      abort.abort()
    }
  }, [job.classId, revisionId])

  if (revisionId === null || loadFailed) return <Notice kind="error">{t('studio.loadFailed')}</Notice>
  if (list === null) return <p>{t('studio.loading')}</p>

  const changeable = CHANGEABLE.includes(job.state) && !stopped
  const busy = act.busy || stop.busy
  const gaps = list.songs.filter((song) => song.gap).length
  const changes = list.changes ?? refused
  const suspected = list.evidence.state === 'suspected_unavailable'

  const run = (action: StudioAction, position: number | null) => {
    const scope = `studio.${revisionId}.${action}.${position === null ? 'all' : String(position)}`
    void act.run(
      async () => {
        const made = await actOnStudioList(job.classId, revisionId, action, position, actionKey(scope))
        dropActionKey(scope)
        rememberStudioAction(made.id, action)
        navigate(`/jobs/${made.id}`)
      },
      (error) => {
        setRefused(refusalChanges(error))
        return studioError(error)
      },
    )
  }

  const tryLater = () => {
    void stop.run(async () => {
      await cancelJob(job.id)
      setStopped(true)
    }, studioError)
  }

  return (
    <Card
      id="studio-songs"
      role="region"
      aria-labelledby="studio-songs-title"
      tabIndex={-1}
      className="flex flex-col gap-3"
    >
      <CardTitle id="studio-songs-title">{t('studio.title')}</CardTitle>
      <p className="m-0 text-muted" data-testid="studio-length">
        {t('studio.length', { length: minutes(list.totalSec), target: minutes(list.targetSec) })}
      </p>
      {list.note !== '' ? <p className="m-0 text-muted">{list.note}</p> : null}
      {suspected && !stopped ? (
        <div className="flex flex-col gap-3 rounded-md border border-line bg-surface p-3" data-testid="studio-suspected">
          <p className="m-0 font-medium text-text">{t('studio.suspected.title')}</p>
          <p className="m-0 text-text">{t('studio.suspected.text', { sent: list.evidence.sent })}</p>
          <p className="m-0 text-muted">{t('studio.suspected.hint')}</p>
          {changeable ? (
            <div className="flex flex-wrap gap-3">
              <Button variant="secondary" disabled={busy} onClick={tryLater}>
                {t('studio.tryLater')}
              </Button>
              <Button disabled={busy} onClick={() => { run('swap-anyway', null) }}>
                {t('studio.swapAnyway')}
              </Button>
            </div>
          ) : null}
        </div>
      ) : null}
      {stopped ? <Notice kind="success">{t('studio.stopped')}</Notice> : null}
      <ol className="m-0 flex list-none flex-col gap-2 p-0" aria-label={t('studio.list')}>
        {list.songs.map((song) => (
          <StudioSongRow
            key={song.position}
            song={song}
            disabled={busy || !changeable}
            onUndo={(position) => { run('undo', position) }}
            onAnother={(position) => { run('another', position) }}
          />
        ))}
      </ol>
      {gaps > 0 && !suspected ? <Notice>{t('studio.gaps', { count: gaps })}</Notice> : null}
      {act.error ? <Notice kind="error">{act.error}</Notice> : null}
      {stop.error ? <Notice kind="error">{stop.error}</Notice> : null}
      {changes !== null ? (
        <p className="m-0 text-muted" data-testid="studio-changes">
          {t('studio.changes', { count: changes.count, limit: changes.limit })}
        </p>
      ) : null}
      <p className="m-0 text-muted">{t('studio.changesNote')}</p>
      <p className="m-0 text-muted" data-testid="studio-credit">{t('studio.credit')}</p>
      <p className="m-0">
        <Link className="button button--secondary" to={startAgainPath(job.classId)}>
          {t('studio.newPick')}
        </Link>
      </p>
    </Card>
  )
}
