import type { CataloguePlan } from '../../api/plan'
import { integer } from '../../i18n/format'
import { useT } from '../../i18n/I18n'

/**
 * What a plan includes, from the public catalogue (`GET /api/plans`): never a price. With no plan yet
 * (the prerender, or a failed call) every row holds a dash, so the layout does not move when the numbers arrive.
 */
export function PlanFacts({ plan }: { plan: CataloguePlan | null }) {
  const t = useT()
  const dash = t('plan.facts.unknown')
  const yes = (included: boolean) => (included ? t('plan.facts.yes') : t('plan.facts.no'))
  const rows: [string, string][] = [
    [t('plan.facts.classes'), plan === null ? dash : integer(plan.limits.classesPerMonth)],
    [t('plan.facts.minutes'), plan === null ? dash : t('plan.facts.minutesValue', { minutes: plan.limits.maxClassMinutes })],
    [t('plan.facts.revisions'), plan === null ? dash : integer(plan.limits.revisionsPerClass)],
    [t('plan.facts.jobs'), plan === null ? dash : integer(plan.limits.concurrentJobs)],
    [t('plan.facts.games'), plan === null ? dash : yes(plan.gates.games)],
    [t('plan.facts.ftp'), plan === null ? dash : yes(plan.gates.ftpTests)],
  ]
  return (
    <dl className="m-0 flex flex-col gap-1" data-testid="plan-facts">
      {rows.map(([label, value]) => (
        <div key={label} className="flex justify-between gap-4 border-b border-line py-1 last:border-b-0">
          <dt className="text-muted">{label}</dt>
          <dd className="m-0 text-right font-medium">{value}</dd>
        </div>
      ))}
    </dl>
  )
}
