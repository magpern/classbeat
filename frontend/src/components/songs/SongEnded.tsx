import { useCallback, useEffect, useState } from 'react'
import { ApiError } from '../../api/client'
import { getClass } from '../../api/classes'
import { failureText } from '../../api/jobMessages'
import { listUncertainOperations, type Job, type Operation } from '../../api/jobs'
import { startError } from '../../api/songMessages'
import {
  createPickJob,
  createReviseSongsJob,
  getSongList,
  PICK_KIND,
  type PickInput,
  type ReviseSongsInput,
} from '../../api/songs'
import { classContext, currentConnectionIds, followUpBrief, isNotConnected } from '../../lib/songs'
import { actionKey, dropActionKey, forgetInput, recallPick, rememberPick } from '../../lib/submittedInput'
import { Link, useRouter } from '../../router/Router'
import { Notice, useAction } from '../forms'
import { Button } from '../ui/button'
import { Card, CardTitle } from '../ui/card'
import { UncertainAttach } from '../classes/UncertainAttach'
import { PLAYLIST_OPERATIONS, UncertainPlaylist } from './UncertainPlaylist'

const isPlaylistWrite = (operation: Operation) => PLAYLIST_OPERATIONS.includes(operation.kind)
const isOtherWrite = (operation: Operation) => !isPlaylistWrite(operation)

function describe(error: unknown): string {
  if (isNotConnected(error)) return startError(new ApiError(409, 'not_connected', null, undefined))
  return startError(error)
}

/**
 * "Create the playlist again" (ADR 0008 section 6): a resume job for a list the user already
 * approved. The approval is the receipt, so nothing is approved again.
 */
function useResume(job: Job) {
  const { navigate } = useRouter()
  const action = useAction()
  const create = () => {
    const revisionId = job.approvalRevisionId
    if (revisionId === null) return
    const scope = `resume.${job.id}`
    void action.run(async () => {
      const ids = await currentConnectionIds()
      const list = await getSongList(job.classId, revisionId)
      let created: Job
      if (job.kind === PICK_KIND) {
        // The class's own brief; this tab's remembered form only when the class has none.
        const context = await classContext(job.classId)
        const kept = recallPick(job.id)
        const input: PickInput = {
          class_id: job.classId,
          brief: followUpBrief(context.brief ?? kept?.brief ?? null, list.targetSec, ''),
          mode: 'resume',
          resume: { song_list_revision_id: revisionId },
          mowl_connection_id: ids.mowl,
          spotify_connection_id: ids.spotify,
        }
        created = await createPickJob(input, actionKey(scope))
        rememberPick(created.id, input)
      } else {
        // ADR 0008 section 6: the class's current draft is the base; nothing to change is sent.
        const base = await currentRevisionId(job.classId)
        const input: ReviseSongsInput = {
          class_id: job.classId,
          base_revision_id: base,
          mode: 'resume',
          resume: { song_list_revision_id: revisionId },
          mowl_connection_id: ids.mowl,
          spotify_connection_id: ids.spotify,
        }
        created = await createReviseSongsJob(input, actionKey(scope))
      }
      dropActionKey(scope)
      forgetInput(job.id)
      navigate(`/jobs/${created.id}`)
    }, describe)
  }
  return { create, action }
}

async function currentRevisionId(classId: string): Promise<string> {
  const found = await getClass(classId)
  if (found.currentRevisionId === null) throw new ApiError(409, 'stale_base', null, undefined)
  return found.currentRevisionId
}

function usePickAgain(job: Job) {
  const { navigate } = useRouter()
  const action = useAction()
  const again = () => {
    const scope = `again.${job.id}`
    void action.run(async () => {
      const kept = recallPick(job.id)
      if (kept === null) {
        navigate('/classes/new')
        return
      }
      const ids = await currentConnectionIds()
      const input: PickInput = {
        class_id: job.classId,
        brief: kept.brief,
        mode: 'pick',
        mowl_connection_id: ids.mowl,
        spotify_connection_id: ids.spotify,
      }
      const created = await createPickJob(input, actionKey(scope))
      dropActionKey(scope)
      rememberPick(created.id, input)
      forgetInput(job.id)
      navigate(`/jobs/${created.id}`)
    }, describe)
  }
  return { again, action, remembered: recallPick(job.id) !== null }
}

/** What a song job that ended without a class shows: failed, stopped or expired. */
export function SongEnded({ job }: { job: Job }) {
  const uncertain = job.state === 'failed' && job.errorCategory === 'uncertain_write'
  const [operations, setOperations] = useState<Operation[] | null>(null)
  const [loadFailed, setLoadFailed] = useState(false)
  const [playlistDone, setPlaylistDone] = useState(false)
  const [otherDone, setOtherDone] = useState(false)
  const onPlaylistResolved = useCallback(() => {
    setPlaylistDone(true)
  }, [])
  const onOtherResolved = useCallback(() => {
    setOtherDone(true)
  }, [])
  useEffect(() => {
    if (!uncertain) return
    let current = true
    listUncertainOperations(job.id).then(
      (all) => {
        if (current) setOperations(all)
      },
      () => {
        if (current) setLoadFailed(true)
      },
    )
    return () => {
      current = false
    }
  }, [uncertain, job.id])
  const playlistOps = operations?.filter(isPlaylistWrite) ?? []
  const otherOps = operations?.filter(isOtherWrite) ?? []
  // Every undecided operation of the job is decided (or none is left) before a resume is offered.
  const decided =
    operations !== null && (playlistOps.length === 0 || playlistDone) && (otherOps.length === 0 || otherDone)
  const resume = useResume(job)
  const pick = usePickAgain(job)
  const approved = job.approvedAt !== null
  const isPick = job.kind === PICK_KIND

  let title: string
  let detail: string
  let account = false
  if (job.state === 'cancelled') {
    title = 'You stopped this'
    detail = approved
      ? 'You stopped the job after approving the songs. The class was not finished.'
      : 'Nothing was written to your Spotify.'
  } else if (job.state === 'expired') {
    title = approved ? 'This job expired' : 'These songs expired'
    detail = approved
      ? 'The job took too long and was ended.'
      : 'Nobody approved the songs in time. Nothing was written to your Spotify.'
  } else {
    const text = failureText(job.errorCategory, job.kind)
    title = text.title
    detail = text.detail
    account = text.account === true
  }

  // A resume is possible only for a list that was approved (the receipt); and not while a write
  // is still undecided.
  const canResume = approved && (!uncertain || decided)
  const error = resume.action.error ?? pick.action.error
  return (
    <Card>
      {uncertain ? (
        <div className="flex flex-col gap-4">
          {loadFailed ? (
            <Notice kind="error">What is left to decide could not be loaded. Reload the page.</Notice>
          ) : operations === null ? (
            <p className="m-0">Checking what happened</p>
          ) : null}
          {playlistOps.length > 0 ? (
            <UncertainPlaylist operations={playlistOps} onResolved={onPlaylistResolved} />
          ) : null}
          {otherOps.length > 0 ? (
            <UncertainAttach jobId={job.id} accept={isOtherWrite} onResolved={onOtherResolved} />
          ) : null}
          {operations !== null && operations.length === 0 ? (
            <Notice>Nothing is left to decide. You can create the playlist again.</Notice>
          ) : null}
        </div>
      ) : (
        <>
          <CardTitle>{title}</CardTitle>
          <p className="m-0 text-text">{detail}</p>
        </>
      )}
      {error ? <Notice kind="error">{error}</Notice> : null}
      <div className="mt-4 flex flex-wrap gap-3">
        {account ? (
          <Button asChild>
            <Link to={job.errorCategory === 'invalid_grant' ? '/account/spotify' : '/account'}>
              {job.errorCategory === 'invalid_grant' ? 'Reconnect Spotify' : 'Go to your account'}
            </Link>
          </Button>
        ) : null}
        {canResume ? (
          <Button variant={account ? 'secondary' : 'primary'} disabled={resume.action.busy} onClick={resume.create}>
            Create the playlist again
          </Button>
        ) : null}
        {!approved && isPick ? (
          <Button
            variant={account ? 'secondary' : 'primary'}
            disabled={pick.action.busy}
            onClick={pick.again}
          >
            {pick.remembered ? 'Pick songs again' : 'Start a new class'}
          </Button>
        ) : null}
        {!isPick ? (
          <Button asChild variant="secondary">
            <Link to={`/classes/${job.classId}`}>Back to the class</Link>
          </Button>
        ) : null}
      </div>
    </Card>
  )
}
