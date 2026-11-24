import { useCallback, useEffect, useState } from 'react'
import { getOutputs, type OutputStatus, type OutputsView } from '../../api/studioOutputStates'

export type OutputsLoad =
  | { status: 'loading' }
  | { status: 'failed' }
  | { status: 'done'; view: OutputsView | null }

/**
 * The class's outputs and their states, read again when the class's open job starts or ends (a
 * `produce_outputs` job changes them) and every few seconds while it is open. `replace` puts in the answer of an action; `retry` reads again.
 */
export function useStudioOutputs(classId: string, enabled: boolean, open: boolean) {
  const [load, setLoad] = useState<{ key: string; value: OutputsLoad } | null>(null)
  const key = classId
  const [attempt, setAttempt] = useState(0)
  const retry = useCallback(() => setAttempt((n) => n + 1), [])
  useEffect(() => {
    if (!enabled) return
    const abort = new AbortController()
    const read = () => {
      getOutputs(classId, abort.signal).then(
        (view) => {
          if (!abort.signal.aborted) setLoad({ key, value: { status: 'done', view } })
        },
        () => {
          // A failed poll keeps the last answer.
          if (!abort.signal.aborted) setLoad((was) => (was?.value.status === 'done' ? was : { key, value: { status: 'failed' } }))
        },
      )
    }
    read()
    // While a job of the class is open the states are read again, so `running` shows and ends.
    const timer = open ? window.setInterval(read, 5000) : undefined
    return () => {
      abort.abort()
      if (timer !== undefined) window.clearInterval(timer)
    }
  }, [classId, enabled, open, key, attempt])
  const replace = useCallback((next: OutputStatus) => {
    setLoad((was) => {
      if (was === null || was.value.status !== 'done' || was.value.view === null) return was
      const outputs = was.value.view.outputs.map((o) =>
        o.revisionId === next.revisionId && o.output === next.output ? next : o,
      )
      return { key: was.key, value: { status: 'done', view: { ...was.value.view, outputs } } }
    })
  }, [])
  const value: OutputsLoad = load?.key === key ? load.value : { status: 'loading' }
  return { load: value, replace, retry }
}
