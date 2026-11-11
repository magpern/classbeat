import { useEffect, useRef } from 'react'
import type { Offer, Subscription } from '../../api/billing'
import { dayText } from '../../api/planMessages'
import { money } from '../../i18n/format'
import { useT } from '../../i18n/I18n'
import { ACTION_LABEL, confirmKey, type Choice } from '../../lib/billingChoice'
import { Button } from '../ui/button'

/**
 * The confirmation of one plan change, with the quote the server returned for it. A buy or an
 * upgrade is a test payment (Dummy provider): the amount is shown, and confirming sends it as the
 * expected amount, so a price that moved in the meantime is refused (409 `quote_changed`).
 */
export function OfferConfirm({
  choice,
  offer,
  name,
  sub,
  busy,
  onConfirm,
  onBack,
}: {
  choice: Choice
  /** The offer chosen; null for a cancel. */
  offer: Offer | null
  name: string
  sub: Subscription | null
  busy: boolean
  onConfirm: () => void
  onBack: () => void
}) {
  const t = useT()
  const group = useRef<HTMLDivElement>(null)
  // The panel opens away from the button that asked for it: move focus to it, so it is read and in view.
  useEffect(() => {
    group.current?.focus()
  }, [])
  const charge = offer?.chargeMinor ?? 0
  const payment = choice.kind === 'buy' || choice.kind === 'upgrade'
  return (
    <div
      ref={group}
      tabIndex={-1}
      role="group"
      aria-label={t(ACTION_LABEL[choice.kind], { name })}
      className="flex flex-col gap-3 rounded-lg border border-line bg-raised p-4"
      data-testid="offer-confirm"
    >
      <p className="m-0" data-testid="confirm-text">
        {t(confirmKey(choice.kind, charge), {
          name,
          amount: money(charge, offer?.currency ?? 'USD'),
          end: dayText(sub?.periodEnd) ?? t('plan.firstFallback'),
        })}
      </p>
      {payment ? <p className="m-0 text-muted">{t('plan.confirm.test')}</p> : null}
      <p className="m-0 flex flex-wrap items-center gap-3">
        <Button disabled={busy} onClick={onConfirm}>
          {t('plan.confirm.go')}
        </Button>
        <Button variant="ghost" disabled={busy} onClick={onBack}>
          {t('plan.confirm.back')}
        </Button>
      </p>
    </div>
  )
}
