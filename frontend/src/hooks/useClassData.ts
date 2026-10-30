import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError } from '../api/client'
import {
  getClass,
  getRevision,
  listMessages,
  listRevisions,
  type ClassMessage,
  type ClassSummary,
  type Revision,
  type RevisionSummary,
} from '../api/classes'

export interface ClassData {
  summary: ClassSummary
  revisions: RevisionSummary[]
  messages: ClassMessage[]
}

export type ClassState =
  | { status: 'loading' }
  | { status: 'not_found' }
  | { status: 'failed' }
  | { status: 'ready'; data: ClassData }

export type RevisionState =
  | { status: 'none' }
  | { status: 'loading' }
  | { status: 'failed' }
  | { status: 'ready'; revision: Revision }

/**
 * Loads one class with its revisions and conversation. `reload` reads all three again and keeps
 * showing the old data meanwhile; only the newest read may set the state, so a slow answer never
 * replaces a newer one. A foreign or unknown id is the same `not_found`.
 */
export function useClassData(classId: string): { state: ClassState; reload: () => void } {
  const [state, setState] = useState<ClassState>({ status: 'loading' })
  const [round, setRound] = useState(0)
  const reload = useCallback(() => {
    setRound((n) => n + 1)
  }, [])

  useEffect(() => {
    const abort = new AbortController()
    const { signal } = abort
    void (async () => {
      try {
        const [summary, revisions, messages] = await Promise.all([
          getClass(classId, signal),
          listRevisions(classId, signal),
          listMessages(classId, signal),
        ])
        if (!signal.aborted) setState({ status: 'ready', data: { summary, revisions, messages } })
      } catch (error) {
        if (signal.aborted) return
        setState(
          error instanceof ApiError && error.status === 404
            ? { status: 'not_found' }
            : // Keep what is shown on a failed reload; the first load has nothing to keep.
              (previous) => (previous.status === 'ready' ? previous : { status: 'failed' }),
        )
      }
    })()
    return () => {
      abort.abort()
    }
  }, [classId, round])

  return { state, reload }
}

/** The revision to show, by id. Revisions never change, so one read per id is kept. */
export function useRevision(classId: string, revisionId: string | null): RevisionState {
  const [results, setResults] = useState<Record<string, RevisionState>>({})
  const requested = useRef(new Set<string>())

  useEffect(() => {
    if (revisionId === null || requested.current.has(revisionId)) return
    requested.current.add(revisionId)
    const finish = (result: RevisionState) => {
      setResults((previous) => ({ ...previous, [revisionId]: result }))
    }
    getRevision(classId, revisionId).then(
      (revision) => {
        finish({ status: 'ready', revision })
      },
      () => {
        requested.current.delete(revisionId) // a later visit may read it again
        finish({ status: 'failed' })
      },
    )
  }, [classId, revisionId])

  if (revisionId === null) return { status: 'none' }
  return results[revisionId] ?? { status: 'loading' }
}
