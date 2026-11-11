import { useCatalogue } from '../../hooks/usePlanView'
import { useT } from '../../i18n/I18n'
import { PRICING_PLANS } from '../../publicSite/pricing'
import { PlanFacts } from './PlanFacts'

/**
 * The plan cards of the public Plans page. The words are static, so the prerender has them; the
 * numbers come from the public catalogue after takeover, into rows that already exist (a dash until
 * then), so nothing moves. The one place a public page reaches the API client, from an effect only
 * (ADR 0030 section 8, like `SupportLink`). Never a price.
 */
export function PricingPlans() {
  const t = useT()
  const catalogue = useCatalogue()
  return (
    <ul className="m-0 flex list-none flex-col gap-4 p-0">
      {PRICING_PLANS.map((plan) => {
        const found = catalogue?.find((p) => p.id === plan.id) ?? null
        return (
          <li
            key={plan.id}
            className="reveal glow-card flex flex-col gap-3 rounded-lg border border-line bg-surface p-5"
            data-testid={`pricing-${plan.id}`}
          >
            <h3 className="m-0 font-display text-lg font-bold">{t(plan.name)}</h3>
            <p className="m-0">{t(plan.blurb)}</p>
            <p className="m-0 text-muted">
              {t('plan.options.quality', { quality: t(`plan.quality.${found?.quality ?? plan.quality}`) })}
            </p>
            <PlanFacts plan={found} />
          </li>
        )
      })}
    </ul>
  )
}
