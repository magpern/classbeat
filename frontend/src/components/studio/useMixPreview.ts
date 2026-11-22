import { useEffect, useRef, useState } from 'react'
import { ApiError } from '../../api/client'
import { previewMix, type MixChoicesBody, type MixPreview, type MixSuggestion } from '../../api/studioMix'
import { studioError } from '../../api/studioSongs'

export type PreviewAnswer =
  | { status: 'loading' }
  | { status: 'failed'; reason: string | null; message: string }
  | { status: 'ready'; preview: MixPreview }

/** The wait after the last change of a choice before the preview is asked (the first one is not delayed). */
export const PREVIEW_DEBOUNCE_MS = 350

/**
 * The read-only preview of the screen's choices (`POST .../mix-choices/preview`), debounced.
 * It stores nothing and starts no job. `answer` is `loading` until the answer for exactly these
 * choices is in, so Approve and Apply never act on an older one; `suggestions` keep the last ones
 * read (they depend on the proposal only) so the rows stay while a new answer is awaited.
 */
export function useMixPreview(
  classId: string,
  revisionId: string,
  contentHash: string,
  choices: MixChoicesBody,
  enabled: boolean,
): { answer: PreviewAnswer; suggestions: readonly MixSuggestion[] } {
  const key = JSON.stringify([classId, revisionId, contentHash, choices])
  const [found, setFound] = useState<{ key: string; answer: PreviewAnswer } | null>(null)
  const [suggestions, setSuggestions] = useState<readonly MixSuggestion[]>([])
  const asked = useRef(false)
  useEffect(() => {
    if (!enabled) return
    let cancelled = false
    const timer = window.setTimeout(
      () => {
        asked.current = true
        previewMix(classId, { revisionId, contentHash, choices }).then(
          (preview) => {
            if (cancelled) return
            setSuggestions(preview.suggestions)
            setFound({ key, answer: { status: 'ready', preview } })
          },
          (error: unknown) => {
            if (cancelled) return
            const reason = error instanceof ApiError ? error.reason : null
            setFound({ key, answer: { status: 'failed', reason, message: studioError(error) } })
          },
        )
      },
      asked.current ? PREVIEW_DEBOUNCE_MS : 0,
    )
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
    // `choices` is in the key: a new object with the same content is the same question.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [classId, revisionId, contentHash, key, enabled])
  const answer: PreviewAnswer = enabled && found?.key === key ? found.answer : { status: 'loading' }
  return { answer, suggestions }
}
