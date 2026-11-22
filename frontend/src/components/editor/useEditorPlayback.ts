import { useState } from 'react'
import type { TransportState } from '../../preview/transport'
import { useRenderedPreview } from './useRenderedPreview'
import { useSilentCursor } from './useSilentCursor'

/**
 * The editor's playback: the rendered preview, or, when the server has no preview to give (the
 * feature is off, 404 `unavailable`), the silent clock cursor the editor had before, for the rest of
 * the page's life. `rendered` says which one the page is drawing.
 */
export function useEditorPlayback(options: { classId: string; workoutMs: number; mixEndMs: number; audioKey: string }): {
  rendered: boolean
  state: TransportState
  audition: (piece: number, sourceMs: number, classMs: number) => void
  pause: () => void
} {
  const preview = useRenderedPreview({
    classId: options.classId,
    mixEndMs: options.mixEndMs,
    audioKey: options.audioKey,
  })
  const silent = useSilentCursor(options.workoutMs)
  const [fallback, setFallback] = useState(false)
  const gone = preview.state.status === 'error' && preview.state.reason === 'unavailable'
  if (gone && !fallback) setFallback(true)
  return fallback ? { rendered: false, ...silent } : { rendered: true, ...preview }
}
