import type { Plan as PlanData } from '../../api/plan'
import { periodMonth } from '../../api/planMessages'
import { t as translate } from '../../i18n/t'

// Its own module so the shell (first load) can word the line without pulling in the whole
// account Plan screen, which is a lazy chunk.

/** "3 of 30 classes in October": the figure the account page shows. */
export function classesLine(plan: PlanData): string {
  const month = periodMonth(plan.periodEnd) ?? translate('plan.thisMonth')
  const limit = plan.limits.classesPerMonth
  return translate('plan.classesLine', { count: limit, held: plan.classesHeld, month })
}
