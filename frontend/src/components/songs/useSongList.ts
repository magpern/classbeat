import { useCallback, useEffect, useState } from 'react'
import type { Job } from '../../api/jobs'
import { getSongList, type SongList } from '../../api/songs'

type Load =
  | { status: 'loading' }
  | { status: 'failed' }
  | { status: 'ready'; list: SongList }

export function useSongList(job: Job) {
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
