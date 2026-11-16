import { useEffect, useState } from 'react'
import type { ClassSummary } from '../../api/classes'
import { getJob } from '../../api/jobs'
import { STUDIO_KIND } from '../../api/studioSongs'
import { isStudio, type StudioView } from './useStudioTimeline'

/** `detecting`: not known yet, so the legacy song panels wait (they would ask for a list a studio class lacks). */
export type ClassKind = 'detecting' | 'studio' | 'other'

/**
 * Whether the class is a studio class: it has a studio draft (ready, or its music changed), or
 * its approved song list was made by a `studio_songs` job and the design never started. The
 * legacy song panels do not apply to it; the studio panel covers its songs. A failed timeline or
 * job read is not a studio class. This is the one read of the approved list's job.
 */
export function useClassKind(summary: ClassSummary, view: StudioView): ClassKind {
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
      () => {
        if (!abort.signal.aborted) setFound({ jobId: probe, studio: false })
      },
    )
    return () => {
      abort.abort()
    }
  }, [probe])
  if (view.status === 'loading') return 'detecting'
  if (isStudio(view)) return 'studio'
  if (probe === null) return 'other'
  if (found?.jobId !== probe) return 'detecting'
  return found.studio ? 'studio' : 'other'
}
