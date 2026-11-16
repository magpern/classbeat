import { useCallback, useEffect, useState } from 'react'
import { getOutputs, type OutputStatus, type OutputsView } from '../../api/studioOutputStates'

export type OutputsLoad =
  | { status: 'loading' }
  | { status: 'failed' }
  | { status: 'done'; view: OutputsView | null }

/**
 * The class's outputs and their states, read again when the class's open job ends (a
 * `produce_outputs` job changes them). `replace` puts in the answer of an action.
 */
export function useStudioOutputs(classId: string, settled: boolean) {
  const [load, setLoad] = useState<{ key: string; value: OutputsLoad } | null>(null)
  const key = `${classId}/${String(settled)}`
  useEffect(() => {
    if (!settled) return
    const abort = new AbortController()
    getOutputs(classId, abort.signal).then(
      (view) => {
        if (!abort.signal.aborted) setLoad({ key, value: { status: 'done', view } })
      },
      () => {
        if (!abort.signal.aborted) setLoad({ key, value: { status: 'failed' } })
      },
    )
    return () => {
      abort.abort()
    }
  }, [classId, settled, key])
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
  return { load: value, replace }
}
