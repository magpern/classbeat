import { useEffect, useState } from 'react'
import { getUsage } from '../../api/usage'

/** "Classes this month" in the sidebar. The limit comes with the plan API; until then, the count. */
export function ClassesThisMonth() {
  const [count, setCount] = useState<number | null>(null)
  useEffect(() => {
    const abort = new AbortController()
    getUsage(abort.signal).then(
      (usage) => {
        if (!abort.signal.aborted) setCount(usage.classesDesigned)
      },
      () => undefined,
    )
    return () => {
      abort.abort()
    }
  }, [])
  if (count === null) return null
  return (
    <p className="m-0 rounded-md bg-raised px-3 py-2 text-[0.8125rem] text-muted">
      Classes this month <strong className="ml-1 text-text tabular-nums">{String(count)}</strong>
    </p>
  )
}
