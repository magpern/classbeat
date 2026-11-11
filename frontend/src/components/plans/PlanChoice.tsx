import { useT } from '../../i18n/I18n'
import { usePlanView } from '../../hooks/usePlanView'
import { lowestPlanFor, type Need } from '../../lib/planUpgrade'
import { Link } from '../../router/Router'
import { Notice } from '../forms'
import { Button } from '../ui/button'

/**
 * Before the design job starts (and takes a class): a request that asks for more than the plan
 * allows says so and offers two ways on. "Design within my plan" starts the design the reading
 * already held to the plan; "See plans" goes to Account, Plan, and starts nothing. No price.
 */
export function PlanChoice({ need, onWithin }: { need: Need; onWithin: () => void }) {
  const t = useT()
  const { plan, catalogue, ready } = usePlanView()
  if (!ready) return <Notice>{t('start.handoff.waiting')}</Notice>
  const lowest = lowestPlanFor(catalogue, plan?.plan ?? null, need)
  return (
    <div className="flex flex-col gap-3" data-testid="plan-choice">
      <Notice kind="info">
        <strong>{t('plan.choice.title')}</strong> {t('plan.choice.body')}
      </Notice>
      <ul className="m-0 flex list-disc flex-col gap-1 pl-5">
        {need.minutes !== null ? (
          <li>
            {plan === null
              ? t('plan.choice.length')
              : t('plan.choice.lengthMax', { minutes: plan.limits.maxClassMinutes })}
          </li>
        ) : null}
        {need.games ? <li>{t('plan.choice.games')}</li> : null}
        {need.ftpTests ? <li>{t('plan.choice.ftp')}</li> : null}
      </ul>
      {lowest === null ? null : <p className="m-0">{t('plan.choice.lowest', { name: lowest.name })}</p>}
      <p className="m-0 flex flex-wrap items-center gap-3">
        <Button onClick={onWithin}>{t('plan.choice.within')}</Button>
        <Link to="/account#plan">{lowest === null ? t('plan.upgrade.link') : t('plan.choice.see', { name: lowest.name })}</Link>
      </p>
    </div>
  )
}
