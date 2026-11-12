import { useState, type FormEvent } from 'react'
import {
  modelError,
  setModelRoute,
  type CatalogEntry,
  type ModelRoute,
  type ModelPlan,
  type StageGroup,
} from '../../api/adminModels'
import { SelectField } from '../../components/classes/fields'
import { Notice, useAction } from '../../components/forms'
import { Button } from '../../components/ui/button'
import { Chip } from '../../components/ui/chip'
import { useAdminWrite } from './StepUp'
import { when } from './Table'

const STAGE_TITLES: Record<StageGroup, string> = { design: 'Design', songs: 'Song picking' }

// Why a route's result is out of date, in words (`ModelRoute.stale`).
const STALE: Record<string, string> = {
  prompt_versions: 'prompts changed since the eval',
  suite_version: 'eval suite is newer',
  manifest_digest: 'eval cases changed',
  newer_failure: 'a newer eval failed',
}

function CurrentRoute({ route }: { route: ModelRoute | undefined }) {
  if (route === undefined) {
    return <p className="m-0 text-muted">No route is set. Jobs use the bootstrap model.</p>
  }
  return (
    <div className="flex flex-col gap-1">
      <p className="m-0" data-testid={`route-${route.routeKey}-${route.stageGroup}`}>
        <strong>
          {route.provider} / {route.model}
        </strong>{' '}
        at effort <strong>{route.effort}</strong>
      </p>
      <p className="m-0 text-[0.9375rem] text-muted">
        Set {when(route.setAt)} by {route.setBy}
      </p>
      {route.refusedByWorker ? (
        <Notice kind="error">
          A newer eval of this model failed. The worker refuses this route until it passes again or another is set.
        </Notice>
      ) : null}
      {route.stale.length > 0 && !route.refusedByWorker ? (
        <p className="m-0 flex flex-wrap gap-2">
          <Chip tone="warn">Stale</Chip>
          <span className="text-[0.9375rem] text-muted">
            {route.stale.map((reason) => STALE[reason] ?? reason).join(', ')}. It keeps working.
          </span>
        </p>
      ) : null}
    </div>
  )
}

function firstEnabled(catalog: CatalogEntry[]): number {
  const found = catalog.findIndex((entry) => entry.enabled)
  return found < 0 ? 0 : found
}

/**
 * One stage of one plan: the model and effort its route maps to, and the form that sets it. The
 * server refuses a model without a passing eval result; the refusal is shown in words and the
 * route above stays as it was.
 */
export function ModelRouteCard({
  plan,
  stage,
  route,
  catalog,
  onChanged,
}: {
  plan: ModelPlan
  stage: StageGroup
  route: ModelRoute | undefined
  catalog: CatalogEntry[]
  onChanged: () => void
}) {
  const write = useAdminWrite()
  const action = useAction()
  const current = catalog.findIndex((e) => e.provider === route?.provider && e.model === route.model)
  const [choice, setChoice] = useState(current >= 0 ? current : firstEnabled(catalog))
  const [effort, setEffort] = useState(route?.effort ?? '')
  const [saved, setSaved] = useState(false)
  const entry = catalog[choice]
  const efforts = entry?.efforts ?? []
  const shownEffort = efforts.includes(effort) ? effort : (efforts[0] ?? '')
  const title = `${plan.name} ${STAGE_TITLES[stage].toLowerCase()}`

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (entry === undefined) return
    setSaved(false)
    const input = { provider: entry.provider, model: entry.model, effort: shownEffort }
    void action.run(() => write(() => setModelRoute(plan.routeKey, stage, input)), modelError).then((done) => {
      if (done === undefined) return
      setSaved(true)
      onChanged()
    })
  }

  return (
    <div role="group" aria-label={title} className="flex flex-col gap-3 border-t border-line pt-3">
      <h4 className="m-0 font-display text-base font-bold">{STAGE_TITLES[stage]}</h4>
      <CurrentRoute route={route} />
      {catalog.length === 0 ? (
        <p className="m-0 text-muted">The model catalog is empty.</p>
      ) : (
        <form onSubmit={submit} aria-label={`Set the ${title} route`} className="flex flex-wrap items-end gap-2">
          <SelectField
            label="Model"
            value={choice}
            onChange={(e) => {
              setChoice(Number(e.target.value))
            }}
          >
            {catalog.map((item, index) => (
              <option key={`${item.provider}/${item.model}`} value={index} disabled={!item.enabled}>
                {item.provider} / {item.model}
                {item.enabled ? '' : ' (not allowed)'}
              </option>
            ))}
          </SelectField>
          <SelectField
            label="Effort"
            value={shownEffort}
            onChange={(e) => {
              setEffort(e.target.value)
            }}
          >
            {efforts.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </SelectField>
          <Button type="submit" disabled={action.busy || entry === undefined}>
            Set route
          </Button>
        </form>
      )}
      {action.error !== null ? <Notice kind="error">{action.error}</Notice> : null}
      {saved ? <Notice kind="success">The route is set.</Notice> : null}
    </div>
  )
}
