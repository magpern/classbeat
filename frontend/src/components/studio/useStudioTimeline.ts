import { useEffect, useRef, useState } from 'react'
import { getTimeline, type TimelineResult } from '../../api/studioOutputs'

export type StudioView = { status: 'loading' } | { status: 'failed' } | ({ status: 'done' } & TimelineResult)

/**
 * The class's studio timeline, read again whenever the current version changes. `none` is a class
 * with no version yet: the page shows nothing for it.
 *
 * `studio` is the summary's flag (#1173, ADR 0036 section 5): only a class whose stored music is
 * the studio's can have a studio draft, so for any other class the server's answer is known
 * (`no_studio_draft`: `no_draft`) and no request is made. `no_draft` rather than `none`, because
 * it is the answer the page used to read, and a composer class whose approved songs were made by
 * a `studio_songs` job (its stored music is set only by the first studio design) is found by it
 * (`useClassKind`).
 */
export function useStudioTimeline(
  classId: string,
  revisionId: string | null,
  enabled: boolean,
  studio: boolean,
): StudioView {
  const key = `${classId}/${revisionId ?? ''}`
  const [found, setFound] = useState<{ key: string; view: StudioView } | null>(null)
  const answered = useRef<string | null>(null)
  useEffect(() => {
    // While a job of the class is open the last answer stays and is not read again; the version it
    // makes is read after. A page loaded with a job open is read once, so the kind settles.
    if (revisionId === null || !studio || (!enabled && answered.current === key)) return
    const abort = new AbortController()
    getTimeline(classId, abort.signal).then(
      (result) => {
        if (abort.signal.aborted) return
        answered.current = key
        setFound({ key, view: { status: 'done', ...result } })
      },
      () => {
        if (abort.signal.aborted) return
        answered.current = key
        setFound({ key, view: { status: 'failed' } })
      },
    )
    return () => {
      abort.abort()
    }
  }, [classId, revisionId, enabled, studio, key])
  if (revisionId === null) return { status: 'done', kind: 'none' }
  if (!studio) return { status: 'done', kind: 'no_draft' }
  return found?.key === key ? found.view : { status: 'loading' }
}

/** A studio class: its chat changes go to `studio_revise`. */
export function isStudio(view: StudioView): boolean {
  return view.status === 'done' && (view.kind === 'ready' || view.kind === 'music_changed')
}
