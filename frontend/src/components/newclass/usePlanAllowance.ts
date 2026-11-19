import { useEffect, useState } from 'react'
import { getPlan, type Gates, type Plan } from '../../api/plan'

/** What the plan allows (#496) and the classes left this month; both stay null while unknown. */
export interface PlanAllowance {
  classesLeft: number | null
  allowed: { maxMinutes: number; gates: Gates } | null
  /** The plan as read, for the gentle nudge. */
  plan: Plan | null
  /** True once the read has finished, whether it worked or not. */
  settled: boolean
}

/** Reads the plan once; a failed call leaves both null and `settled` true (no cap is shown and the server still enforces it). */
export function usePlanAllowance(): PlanAllowance {
  const [state, setState] = useState<PlanAllowance>({ classesLeft: null, allowed: null, plan: null, settled: false })
  useEffect(() => {
    const abort = new AbortController()
    getPlan(abort.signal).then(
      (plan) => {
        if (!abort.signal.aborted) {
          setState({
            classesLeft: Math.max(0, plan.limits.classesPerMonth - plan.classesHeld),
            allowed: { maxMinutes: plan.limits.maxClassMinutes, gates: plan.gates },
            plan,
            settled: true,
          })
        }
      },
      () => {
        if (!abort.signal.aborted) setState((prev) => ({ ...prev, settled: true }))
      },
    )
    return () => {
      abort.abort()
    }
  }, [])
  return state
}
