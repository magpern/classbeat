import { useT } from '../../i18n/I18n'
import type { PlanAllowance } from '../newclass/usePlanAllowance'

/**
 * The Length card's note: the plan's class length once known, a skeleton line while the plan is still loading,
 * and a plain sentence when it could not be read (the server still enforces the cap), so the card is never empty.
 */
export function PlanLength({ allowed, settled }: { allowed: PlanAllowance['allowed']; settled: boolean }) {
  const t = useT()
  if (allowed !== null) {
    return (
      <p className="m-0 text-[0.875rem] text-muted" data-testid="plan-length">
        {t('start.planLength', { minutes: allowed.maxMinutes })}
      </p>
    )
  }
  if (!settled) {
    return (
      <p
        className="m-0 h-5 w-3/4 rounded-sm bg-raised motion-safe:animate-pulse"
        data-testid="plan-length-loading"
        role="status"
        aria-label={t('common.loading')}
      />
    )
  }
  return (
    <p className="m-0 text-[0.875rem] text-muted" data-testid="plan-length-unknown">
      {t('start.planLength.unknown')}
    </p>
  )
}
