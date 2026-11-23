import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { refusalReason, isStale, pickStep, postEdit, type EditOp } from '../../api/classEdits'
import { pieceLabels } from '../../lib/editor/clips'
import { pendingOf, stillApplies, type PendingEdit } from '../../lib/editor/pendingEdit'
import { loadEditor, type EditorData, type EditorLoad } from './loadEditor'

/** `round` counts the reads: a state of round n is the answer to the n-th read. */
export type EditorState = { status: 'loading' } | { status: 'failed' } | ({ status: 'done'; round: number } & EditorLoad)

/** What the last edit, undo or redo left for the page to say. */
type Held =
  | null
  | { kind: 'stale'; pending: PendingEdit | null; afterRound: number }
  | { kind: 'refused'; reason: string }
  | { kind: 'failed' }

export type Outcome =
  | null
  /** The class moved under the page (another tab or a finished job): reloaded. `pending` is the unsent edit kept to apply again. */
  | { kind: 'stale'; pending: PendingEdit | null; applies: boolean }
  /** The server refused the edit by a reason code: nothing changed. */
  | { kind: 'refused'; reason: string }
  | { kind: 'failed' }

export interface ClassEditor {
  state: EditorState
  busy: boolean
  outcome: Outcome
  apply: (op: EditOp) => void
  undo: () => void
  redo: () => void
  /** Applies the edit kept across a stale 409, on the reloaded draft. */
  reapply: () => void
  dismiss: () => void
  reload: () => void
}

/**
 * The editor's state and its edits. Every edit sends the draft it was made on as `base_revision_id`
 * and every undo or redo the current draft as `expected_current_id`; on a 409 `stale_revision`
 * the page reloads, keeps the unsent edit and offers it again (only when it still means the same
 * block). The new content is never taken from an edit's answer: it is read again.
 */
export function useClassEditor(classId: string): ClassEditor {
  const [state, setState] = useState<EditorState>({ status: 'loading' })
  const [round, setRound] = useState(0)
  const roundRef = useRef(0)
  const [inFlight, setBusy] = useState(false)
  const loadedRound = useRef(0) // the read the page's data came from
  const [held, setOutcome] = useState<Held>(null)
  const current = useRef<EditorData | null>(null)
  const working = useRef(false)

  useEffect(() => {
    const abort = new AbortController()
    loadEditor(classId, abort.signal).then(
      (found) => {
        if (abort.signal.aborted) return
        current.current = found.kind === 'ready' ? found.data : null
        loadedRound.current = round
        setState({ status: 'done', round, ...found })
      },
      () => {
        if (!abort.signal.aborted) setState({ status: 'failed' })
      },
    )
    return () => {
      abort.abort()
    }
  }, [classId, round])

  const reload = useCallback(() => {
    roundRef.current += 1
    setRound(roundRef.current)
  }, [])

  const run = useCallback(
    async (work: (data: EditorData) => Promise<void>, pending: PendingEdit | null) => {
      const data = current.current
      // Nothing is sent from a page that is still reading the draft its last edit made: it would be a stale edit.
      if (data === null || working.current || loadedRound.current < roundRef.current) return
      working.current = true
      setBusy(true)
      setOutcome(null)
      try {
        await work(data)
        reload()
      } catch (error) {
        const reason = refusalReason(error)
        if (isStale(error)) setOutcome({ kind: 'stale', pending, afterRound: roundRef.current + 1 })
        else if (reason !== null) setOutcome({ kind: 'refused', reason })
        else setOutcome({ kind: 'failed' })
        // A refusal that says the draft can no longer be edited, and a stale answer, need the new truth.
        if (reason !== null && (isStale(error) || reason === 'music_changed' || reason === 'mix_not_applied' || reason === 'class_busy' || reason === 'hash_mismatch')) reload()
      } finally {
        working.current = false
        setBusy(false)
      }
    },
    [reload],
  )

  const apply = useCallback(
    (op: EditOp) => {
      const data = current.current
      if (data === null) return
      const pending = pendingOf(op, data.blocks, pieceLabels(data.timeline))
      void run(async (d) => {
        await postEdit(classId, d.revisionId, op)
      }, pending)
    },
    [classId, run],
  )

  const step = useCallback(
    (which: 'undo' | 'redo') => {
      const picked = current.current?.overlay[which]
      if (picked === null || picked === undefined) return
      void run(async (d) => {
        await pickStep(classId, picked, d.revisionId)
      }, null)
    },
    [classId, run],
  )
  const undo = useCallback(() => {
    step('undo')
  }, [step])
  const redo = useCallback(() => {
    step('redo')
  }, [step])

  // After the reload that follows a stale answer, the kept edit is checked against the new blocks.
  const outcome: Outcome = useMemo(() => {
    if (held?.kind !== 'stale') return held
    const ready = state.status === 'done' && state.kind === 'ready' && state.round >= held.afterRound // the reload this answer asked for has come back
    const applies =
      held.pending !== null && ready && stillApplies(held.pending, state.data.blocks, pieceLabels(state.data.timeline))
    return { kind: 'stale', pending: held.pending, applies }
  }, [held, state])

  const reapply = useCallback(() => {
    if (outcome?.kind !== 'stale' || outcome.pending === null || !outcome.applies) return
    const op = outcome.pending.op
    const data = current.current
    if (data === null) return
    void run(async (d) => {
      await postEdit(classId, d.revisionId, op)
    }, null)
  }, [classId, outcome, run])

  const dismiss = useCallback(() => {
    setOutcome(null)
  }, [])

  // Busy while an edit is in flight and until the page has read the draft it made.
  const busy = inFlight || (state.status === 'done' && state.round < round)
  return { state, busy, outcome, apply, undo, redo, reapply, dismiss, reload }
}
