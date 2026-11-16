import { useEffect, useState } from 'react'
import type { ClassSummary } from '../../api/classes'
import { getJob } from '../../api/jobs'
import { STUDIO_KIND } from '../../api/studioSongs'
import { isStudio, type StudioView } from './useStudioTimeline'

/**
 * Whether the class is a studio class: it has a studio draft (ready, or its music changed), or
 * its approved song list was made by a `studio_songs` job and the design never started. The
 * legacy song panels do not apply to it; the studio panel covers its songs.
 */
export function useIsStudioClass(summary: ClassSummary, view: StudioView): boolean {
  const { state, jobId } = summary.songs
  const probe = view.status === 'done' && view.kind === 'no_draft' && state === 'approved' ? jobId : null
  const [found, setFound] = useState<{ jobId: string; studio: boolean } | null>(null)
  useEffect(() => {
    if (probe === null) return
    const abort = new AbortController()
    getJob(probe, abort.signal).then(
      (job) => {
        if (!abort.signal.aborted) setFound({ jobId: probe, studio: job.kind === STUDIO_KIND })
      },
      () => undefined,
    )
    return () => {
      abort.abort()
    }
  }, [probe])
  return isStudio(view) || (probe !== null && found?.jobId === probe && found.studio)
}
