import type { Offer, Subscription } from '../../api/billing'
import type { CataloguePlan } from '../../api/plan'
import { money } from '../../i18n/format'
import { useT } from '../../i18n/I18n'
import { ACTION_LABEL, offerKind, type ChoiceKind } from '../../lib/billingChoice'
import { cn } from '../../lib/cn'
import { Button } from '../ui/button'
import { PlanFacts } from './PlanFacts'

/**
 * One plan on the Plan screen. `offer` is null for everyone who may not buy (and for Free): then the
 * card shows what the plan includes and nothing else, never a price or a buy action.
 */
export function PlanCard({
  plan,
  current,
  offer,
  sub,
  busy,
  onChoose,
}: {
  plan: CataloguePlan
  current: boolean
  offer: Offer | null
  sub: Subscription | null
  busy: boolean
  onChoose: (kind: ChoiceKind) => void
}) {
  const t = useT()
  const kind = offer === null ? null : offerKind(offer, sub)
  const priced = offer !== null && offer.priceMinor !== null && offer.currency !== null
  return (
    <li
      className={cn(
        'flex flex-col gap-3 rounded-lg border-2 p-5',
        current
          ? 'border-transparent bg-[linear-gradient(var(--cb-surface),var(--cb-surface))_padding-box,var(--cb-heat)_border-box] shadow-glow'
          : 'border-line bg-surface',
      )}
      data-testid={`plan-card-${plan.id}`}
      data-current={current ? 'true' : undefined}
    >
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="m-0 font-display text-lg font-bold">{plan.name}</h3>
        {current ? (
          <span className="rounded-full bg-heat px-3 py-0.5 text-xs font-bold text-on-heat">
            {t('plan.options.current')}
          </span>
        ) : null}
      </div>
      <p className="m-0 text-muted">{t('plan.options.quality', { quality: t(`plan.quality.${plan.quality}`) })}</p>
      <PlanFacts plan={plan} />
      {offer === null ? null : (
        <div className="flex flex-col gap-2" data-testid={`offer-${plan.id}`}>
          <p className="m-0 font-medium">
            {priced
              ? t('plan.offer.price', { amount: money(offer.priceMinor ?? 0, offer.currency ?? '') })
              : t('plan.offer.noPrice')}
          </p>
          {offer.action === 'upgrade' && offer.chargeMinor !== null ? (
            <p className="m-0 text-muted" data-testid={`quote-${plan.id}`}>
              {t('plan.offer.quote', { amount: money(offer.chargeMinor, offer.currency ?? '') })}
            </p>
          ) : null}
          {kind === null ? null : (
            <p className="m-0">
              <Button
                variant={kind === 'upgrade' || kind === 'buy' ? 'primary' : 'secondary'}
                disabled={busy}
                onClick={() => {
                  onChoose(kind)
                }}
              >
                {t(ACTION_LABEL[kind], { name: plan.name })}
              </Button>
            </p>
          )}
        </div>
      )}
    </li>
  )
}
