import { useId, useState } from 'react'
import type { Plan } from '../../api/plan'
import { useCatalogue } from '../../hooks/usePlanView'
import { useT } from '../../i18n/I18n'
import { claimNudge, dismissNudge, nudgeDismissed } from '../../lib/nudge'
import { nextPlan } from '../../lib/planUpgrade'
import { Link } from '../../router/Router'
import { Button } from '../ui/button'

/** When it shows: one class left this month, the session's first refusal, or once on the account page. */
export type NudgeTrigger = 'low' | 'refusal' | 'account'

/**
 * The gentle nudge toward Individual, for a Free user only (M7a-4). Never a modal and nothing in its
 * way: a line and a link under the content, with "Not now". After a dismissal no trigger shows it
 * again in the session. No price, no countdown. The caller passes the plan it already read.
 */
export function PlanNudge({ trigger, plan }: { trigger: NudgeTrigger; plan: Plan | null }) {
  const t = useT()
  const owner = useId()
  const catalogue = useCatalogue()
  const [gone, setGone] = useState(false)
  // The once-only triggers ask for their place once, as this owner (the id is stable across re-renders).
  const [granted] = useState(() => trigger === 'low' || claimNudge(trigger, owner))
  if (!granted || gone || nudgeDismissed() || plan === null || plan.plan !== 'free') return null
  if (trigger === 'low' && plan.limits.classesPerMonth - plan.classesHeld !== 1) return null
  const next = nextPlan(catalogue, plan.plan)
  if (next === null) return null
  return (
    <aside className="notice flex flex-col gap-2" data-kind="info" aria-label={t('plan.nudge.label')} data-testid="plan-nudge">
      <p className="m-0">
        {t('plan.nudge.body', {
          name: next.name,
          classes: t('plan.classes', { count: next.limits.classesPerMonth }),
          minutes: next.limits.maxClassMinutes,
        })}
      </p>
      <p className="m-0 flex flex-wrap items-center gap-3">
        <Link to="/account#plan">{t('plan.nudge.link', { name: next.name })}</Link>
        <Button
          variant="ghost"
          onClick={() => {
            dismissNudge()
            setGone(true)
          }}
        >
          {t('plan.nudge.dismiss')}
        </Button>
      </p>
    </aside>
  )
}
