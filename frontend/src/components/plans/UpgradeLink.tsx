import { usePlanView } from '../../hooks/usePlanView'
import { useT } from '../../i18n/I18n'
import { Link } from '../../router/Router'
import { PlanNudge } from './PlanNudge'

/**
 * Under a refusal that a bigger plan would lift (`isUpgradeRefusal`): the way to the plans, never a
 * price. On Free the first refusal of the session also carries the gentle nudge.
 */
export function UpgradeLink() {
  const t = useT()
  const { plan } = usePlanView()
  return (
    <div className="flex flex-col gap-3" data-testid="upgrade">
      <p className="m-0">
        <Link to="/account#plan">{t('plan.upgrade.link')}</Link>
      </p>
      <PlanNudge trigger="refusal" plan={plan} />
    </div>
  )
}
