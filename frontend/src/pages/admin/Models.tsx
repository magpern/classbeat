import { useCallback, useEffect, useState } from 'react'
import { adminError } from '../../api/admin'
import { STAGE_GROUPS, getModels, type ModelsView } from '../../api/adminModels'
import { Notice } from '../../components/forms'
import { Chip } from '../../components/ui/chip'
import { ModelRouteCard } from './ModelRouteCard'
import { isReason, useMfaRequired } from './StepUp'
import { SectionTitle } from './Table'

/**
 * The model routes (ADR 0012 section 5, ADR 0031 section 8): for each plan its route key, the
 * quality word users see (Standard, Better, Best) and the model and effort each stage maps to.
 * An admin sets a route here; the server refuses a model without a passing eval and the page
 * says so. Model names appear in this admin area only.
 */
export function Models() {
  const onMfaRequired = useMfaRequired()
  const [view, setView] = useState<ModelsView | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [round, setRound] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    getModels(controller.signal).then(
      (found) => {
        setError(null)
        setView(found)
      },
      (caught: unknown) => {
        if (controller.signal.aborted) return
        if (isReason(caught, 403, 'mfa_required')) onMfaRequired()
        else setError(adminError(caught))
      },
    )
    return () => {
      controller.abort()
    }
  }, [round, onMfaRequired])

  const reload = useCallback(() => {
    setRound((n) => n + 1)
  }, [])

  return (
    <section aria-labelledby="admin-models-title">
      <SectionTitle id="admin-models-title">Model routes</SectionTitle>
      <p className="m-0 mb-4 text-muted">
        A plan&apos;s jobs use the routes of its route key. A plan with no route uses the bootstrap model. A model must
        have passed the eval suite before it can be set.
      </p>
      {error !== null ? <Notice kind="error">{error}</Notice> : null}
      {view === null && error === null ? <p>Loading</p> : null}
      {view !== null ? (
        <div className="flex flex-col gap-4">
          {view.plans.map((plan) => (
            <section key={plan.id} className="panel" aria-labelledby={`model-plan-${plan.id}`}>
              <h3 id={`model-plan-${plan.id}`} className="panel__title">
                {plan.name}
              </h3>
              <p className="m-0 mb-3 flex flex-wrap items-center gap-2">
                <Chip tone="ok" data-testid={`quality-${plan.id}`}>
                  {plan.quality}
                </Chip>
                <span className="text-muted">
                  Route key <code>{plan.routeKey}</code>
                </span>
              </p>
              <div className="flex flex-col gap-4">
                {STAGE_GROUPS.map((stage) => (
                  <ModelRouteCard
                    key={stage}
                    plan={plan}
                    stage={stage}
                    route={view.routes.find((r) => r.routeKey === plan.routeKey && r.stageGroup === stage)}
                    catalog={view.catalog}
                    onChanged={reload}
                  />
                ))}
              </div>
            </section>
          ))}
        </div>
      ) : null}
    </section>
  )
}
