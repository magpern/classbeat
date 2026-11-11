import { useEffect, useState } from 'react'
import { getPlan, getPlanCatalogue, type CataloguePlan, type Plan } from '../api/plan'

/** The public catalogue alone (null while unknown or after a failed call). */
export function useCatalogue(): CataloguePlan[] | null {
  return useCatalogueResult().catalogue
}

/** The catalogue, and whether its call failed (so a screen can say so instead of loading forever). */
export function useCatalogueResult(): { catalogue: CataloguePlan[] | null; failed: boolean } {
  const [catalogue, setCatalogue] = useState<CataloguePlan[] | null>(null)
  const [failed, setFailed] = useState(false)
  useEffect(() => {
    const abort = new AbortController()
    getPlanCatalogue(abort.signal).then(
      (found) => {
        if (!abort.signal.aborted) setCatalogue(found)
      },
      () => {
        if (!abort.signal.aborted) setFailed(true)
      },
    )
    return () => {
      abort.abort()
    }
  }, [])
  return { catalogue, failed }
}

export interface PlanView {
  plan: Plan | null
  catalogue: CataloguePlan[] | null
  /** Both calls have answered (or failed), so the first render does not flash a half-known state. */
  ready: boolean
}

/** The user's plan and the public catalogue, each null while unknown or after a failed call (the server still enforces). */
export function usePlanView(): PlanView {
  const [view, setView] = useState<PlanView>({ plan: null, catalogue: null, ready: false })
  useEffect(() => {
    const abort = new AbortController()
    const settle = <T,>(promise: Promise<T>): Promise<T | null> => promise.then((v) => v, () => null)
    void Promise.all([settle(getPlan(abort.signal)), settle(getPlanCatalogue(abort.signal))]).then(([plan, catalogue]) => {
      if (!abort.signal.aborted) setView({ plan, catalogue, ready: true })
    })
    return () => {
      abort.abort()
    }
  }, [])
  return view
}
