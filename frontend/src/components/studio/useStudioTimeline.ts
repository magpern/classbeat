import { useEffect, useState } from 'react'
import { getTimeline, type TimelineResult } from '../../api/studioOutputs'

export type StudioView = { status: 'loading' } | { status: 'failed' } | ({ status: 'done' } & TimelineResult)

/**
 * The class's studio timeline, read again whenever the current version changes. `none` is every
 * class that is not a studio class (or has no studio draft yet): the page shows nothing for it.
 */
export function useStudioTimeline(classId: string, revisionId: string | null, enabled: boolean): StudioView {
  const key = `${classId}/${revisionId ?? ''}`
  const [found, setFound] = useState<{ key: string; view: StudioView } | null>(null)
  useEffect(() => {
    // While a job of the class is open the last answer stays; the version it makes is read after.
    if (!enabled || revisionId === null) return
    const abort = new AbortController()
    getTimeline(classId, abort.signal).then(
      (result) => {
        if (!abort.signal.aborted) setFound({ key, view: { status: 'done', ...result } })
      },
      () => {
        if (!abort.signal.aborted) setFound({ key, view: { status: 'failed' } })
      },
    )
    return () => {
      abort.abort()
    }
  }, [classId, revisionId, enabled, key])
  if (revisionId === null) return { status: 'done', kind: 'none' }
  return found?.key === key ? found.view : { status: 'loading' }
}

/** A studio class: its chat changes go to `studio_revise`. */
export function isStudio(view: StudioView): boolean {
  return view.status === 'done' && (view.kind === 'ready' || view.kind === 'music_changed')
}
