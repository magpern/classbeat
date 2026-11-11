import { JoinBetaBand } from '../components/publicPages/JoinBetaBand'
import { PricingPlans } from '../components/plans/PricingPlans'
import { useT } from '../i18n/I18n'
import { SAME_ON_EVERY_PLAN } from '../publicSite/pricing'

/** Plans (M7a-5, #950): what each plan includes, with no price. A draft until the owner has seen it. */
export function PricingPage() {
  const t = useT()
  return (
    <article className="mx-auto flex max-w-3xl flex-col gap-8" aria-labelledby="pricing-title">
      <header className="flex flex-col gap-3">
        <h1 id="pricing-title" className="m-0 font-display text-4xl font-extrabold leading-tight tracking-tight">
          {t('pricing.title')}
        </h1>
        <p className="m-0 text-lg">{t('pricing.lead')}</p>
      </header>
      <section aria-labelledby="pricing-plans" className="flex flex-col gap-3">
        <h2 id="pricing-plans" className="m-0 font-display text-2xl font-bold">
          {t('pricing.plans.title')}
        </h2>
        <PricingPlans />
        <p className="m-0 text-muted">{t('pricing.quality.note')}</p>
      </section>
      <section aria-labelledby="pricing-same" className="flex flex-col gap-3">
        <h2 id="pricing-same" className="m-0 font-display text-2xl font-bold">
          {t('pricing.same.title')}
        </h2>
        <ul className="m-0 flex list-disc flex-col gap-2 pl-5">
          {SAME_ON_EVERY_PLAN.map((key) => (
            <li key={key}>{t(key)}</li>
          ))}
        </ul>
      </section>
      <JoinBetaBand />
    </article>
  )
}
