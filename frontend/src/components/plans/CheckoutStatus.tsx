import { useT } from '../../i18n/I18n'
import type { CheckoutStatus as Status } from '../../hooks/useCheckoutWait'
import { Notice } from '../forms'
import { Button } from '../ui/button'

/**
 * Where a hosted checkout stands (ADR 0032 section 7): on the way, waiting for the confirmation, slow (the
 * plan will update by itself), or failed (the plan is unchanged). `landed` and `idle` show nothing here: the
 * plan line and the done note say it.
 */
export function CheckoutStatus({ status, onAgain, onDismiss }: { status: Status; onAgain: () => void; onDismiss: () => void }) {
  const t = useT()
  if (status === 'redirecting') return <Notice>{t('plan.checkout.redirecting')}</Notice>
  if (status === 'waiting') {
    return (
      <div data-testid="checkout-waiting">
        <Notice>{t('plan.checkout.waiting')}</Notice>
      </div>
    )
  }
  if (status === 'slow') {
    return (
      <div className="flex flex-col gap-2" data-testid="checkout-slow">
        <Notice>{t('plan.checkout.slow')}</Notice>
        <p className="m-0 flex flex-wrap gap-3">
          <Button variant="secondary" onClick={onAgain}>
            {t('class.checkAgain')}
          </Button>
          <Button variant="ghost" onClick={onDismiss}>
            {t('dialog.close')}
          </Button>
        </p>
      </div>
    )
  }
  if (status === 'failed') {
    return (
      <div className="flex flex-col gap-2" data-testid="checkout-failed">
        <Notice kind="error">{t('plan.checkout.failed')}</Notice>
        <p className="m-0">
          <Button variant="ghost" onClick={onDismiss}>
            {t('dialog.close')}
          </Button>
        </p>
      </div>
    )
  }
  return null
}
