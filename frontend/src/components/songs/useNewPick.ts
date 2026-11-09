import { useRef } from 'react'
import { ApiError } from '../../api/client'
import { cancelJob, type Job } from '../../api/jobs'
import { startError } from '../../api/songMessages'
import { createPickJob, type PickInput, type SongList } from '../../api/songs'
import { t as translate } from '../../i18n/t'
import {
  classContext,
  currentConnectionIds,
  digest,
  followUpBrief,
  isNotConnected,
  startAfterCancel,
} from '../../lib/songs'
import { actionKey, dropActionKey, recallPick, rememberPick } from '../../lib/submittedInput'
import { useRouter } from '../../router/Router'
import { useAction } from '../forms'

/**
 * Starts a new pick for the class of `job` after stopping the waiting one (ADR 0008 section 5):
 * the two calls are sequential, and a `class_busy` answer to the second waits for the first.
 */
export function useNewPick(job: Job, list: SongList) {
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
