import { useEffect, useState } from 'react'
import { getPlan, type Plan } from '../../api/plan'
import { classesLine } from '../account/Plan'
import { Progress } from '../ui/progress'

function share(plan: Plan): number {
  const limit = plan.limits.classesPerMonth
  return limit > 0 ? Math.min(100, Math.round((plan.classesHeld / limit) * 100)) : 0
}

/**
 * "Classes this month" meter of the sidebar and the icon rail: classes held against the limit of
 * the plan, worded by `classesLine` of the account page. A plan that cannot be read hides it.
 */
export function ClassesThisMonth({ compact = false }: { compact?: boolean }) {
  const [plan, setPlan] = useState<Plan | null>(null)
  useEffect(() => {
    const abort = new AbortController()
    getPlan(abort.signal).then(
      (found) => {
        if (!abort.signal.aborted) setPlan(found)
      },
      () => undefined,
    )
    return () => {
      abort.abort()
    }
  }, [])
  if (plan === null) return null
  const limit = plan.limits.classesPerMonth
  const label = `Classes this month: ${classesLine(plan)}`
  if (compact) {
    // The icon rail: held over limit with the bar, the full words for screen readers.
    return (
      <div className="flex w-full flex-col items-center gap-1 rounded-md bg-raised px-1 py-2 tabular-nums">
        <p className="m-0 text-center text-text">
          <span className="sr-only">{label}</span>
          <strong aria-hidden="true">
            {String(plan.classesHeld)}/{String(limit)}
          </strong>
        </p>
        <Progress value={share(plan)} aria-label="Classes this month" className="h-1 w-10" />
      </div>
    )
  }
  return (
    <div className="flex flex-col gap-2 rounded-md bg-raised px-3 py-2 text-[0.8125rem]">
      <p className="m-0 text-muted">
        Classes this month
        <strong className="ml-1 block text-text tabular-nums">{classesLine(plan)}</strong>
      </p>
      <Progress value={share(plan)} aria-label="Classes this month" className="h-1.5" />
    </div>
  )
}
