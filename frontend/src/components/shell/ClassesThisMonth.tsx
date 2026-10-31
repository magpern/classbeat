import { useEffect, useState } from 'react'
import { getUsage } from '../../api/usage'

/** "Classes this month" in the sidebar. The limit comes with the plan API; until then, the count. */
export function ClassesThisMonth({ compact = false }: { compact?: boolean }) {
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
  if (compact) {
    // The icon rail: the number with a short mark, the full words for screen readers.
    return (
      <p className="m-0 w-full rounded-md bg-raised px-1 py-2 text-center text-text tabular-nums">
        <span className="sr-only">Classes this month: </span>
        <strong>{String(count)}</strong>
        <span aria-hidden="true" className="block text-[0.6875rem] text-muted">
          /mo
        </span>
      </p>
    )
  }
  return (
    <p className="m-0 rounded-md bg-raised px-3 py-2 text-[0.8125rem] text-muted">
      Classes this month <strong className="ml-1 text-text tabular-nums">{String(count)}</strong>
    </p>
  )
}
