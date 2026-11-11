import { useRef, useState } from 'react'
import { changePlan, checkout, type Billing } from '../../api/billing'
import { billingError, isQuoteChanged } from '../../api/billingMessages'
import { ApiError } from '../../api/client'
import type { Plan } from '../../api/plan'
import { dayText } from '../../api/planMessages'
import { useBilling } from '../../hooks/useBilling'
import { useCatalogueResult } from '../../hooks/usePlanView'
import { useT } from '../../i18n/I18n'
import { ACTION_LABEL, canCancel, doneKey, stillOffered, type Choice, type ChoiceKind } from '../../lib/billingChoice'
import { newIdempotencyKey } from '../../lib/submittedInput'
import { Notice, useAction } from '../forms'
import { Button } from '../ui/button'
import { OfferConfirm } from './OfferConfirm'
import { PlanCard } from './PlanCard'

/**
 * The three plans on Account, Plan (M7a-5). Everyone sees what each plan includes. Only a tester or an
 * admin (the server's `GET /api/account/billing` answers, anyone else gets 403) also sees prices, the
 * quote and the buy, upgrade, downgrade, cancel and resume actions, marked as test payments. For the
 * rest: "Plans are not open yet", and no price is requested twice or shown.
 */
export function PlanOffers({ plan, onChanged }: { plan: Plan; onChanged: () => void }) {
  const t = useT()
  const { catalogue, failed: catalogueFailed } = useCatalogueResult()
  const { state, reload, replace } = useBilling()
  const action = useAction()
  const [choice, setChoice] = useState<Choice | null>(null)
  const [done, setDone] = useState<string | null>(null)
  // One key per attempt: a retry after a lost answer is the same request, a refusal starts a new one.
  const attempt = useRef<string | null>(null)

  const billing: Billing | null = state.status === 'ready' ? state.billing : null
  const sub = billing?.subscription ?? null
  const nameOf = (id: string) => catalogue?.find((p) => p.id === id)?.name ?? billing?.offers.find((o) => o.plan === id)?.name ?? id
  const offerOf = (id: string) => billing?.offers.find((o) => o.plan === id) ?? null

  const choose = (kind: ChoiceKind, id: string) => {
    action.clear()
    setDone(null)
    attempt.current = null
    setChoice({ kind, plan: id })
  }

  const confirm = () => {
    if (choice === null || billing === null) return
    const chosen = choice
    const key = (attempt.current ??= newIdempotencyKey())
    void action
      .run(async () => {
        try {
          const next =
            chosen.kind === 'buy' || chosen.kind === 'upgrade'
              ? await checkout(chosen.plan, offerOf(chosen.plan)?.chargeMinor ?? 0, key)
              : await changePlan(chosen.plan, key)
          attempt.current = null
          return next
        } catch (error) {
          if (error instanceof ApiError) attempt.current = null
          if (isQuoteChanged(error)) {
            // The amount moved: show the new quote and ask again, never charge the old one.
            const fresh = await reload()
            if (fresh === null || !stillOffered(fresh, chosen)) setChoice(null)
          }
          throw error
        }
      }, billingError)
      .then((next) => {
        if (next === undefined) return
        replace(next)
        setChoice(null)
        setDone(t(doneKey(chosen.kind), { name: nameOf(chosen.plan) }))
        onChanged()
      })
  }

  const loading = !catalogueFailed && (catalogue === null || state.status === 'loading')
  const open = choice !== null && billing !== null && stillOffered(billing, choice) ? choice : null
  return (
    <div className="flex flex-col gap-3" data-testid="plan-options">
      <h3 className="m-0 font-display text-lg font-bold">{t('plan.options.title')}</h3>
      {loading ? <p className="m-0">{t('common.loading')}</p> : null}
      {catalogueFailed ? <Notice kind="error">{t('plan.billing.loadFailed')}</Notice> : null}
      {!loading && state.status === 'closed' ? <Notice>{t('plan.options.closed')}</Notice> : null}
      {!loading && !catalogueFailed && state.status === 'failed' ? <Notice kind="error">{t('plan.billing.loadFailed')}</Notice> : null}
      {!loading && billing !== null ? <Notice>{t('plan.options.testing')}</Notice> : null}
      {sub !== null && sub.status === 'active' ? (
        <p className="m-0" data-testid="plan-subscription">
          {t(sub.cancelAtEnd ? 'plan.sub.cancels' : sub.nextPlan !== null ? 'plan.sub.downgrades' : 'plan.sub.renews', {
            name: nameOf(sub.plan),
            next: sub.nextPlan === null ? '' : nameOf(sub.nextPlan),
            end: dayText(sub.periodEnd) ?? '',
          })}
        </p>
      ) : null}
      {done !== null ? <Notice kind="success">{done}</Notice> : null}
      {action.error ? <Notice kind="error">{action.error}</Notice> : null}
      {open === null ? null : (
        <OfferConfirm
          choice={open}
          offer={offerOf(open.plan)}
          name={nameOf(open.kind === 'cancel' ? (sub?.plan ?? '') : open.plan)}
          sub={sub}
          busy={action.busy}
          onConfirm={confirm}
          onBack={() => {
            setChoice(null)
            action.clear()
          }}
        />
      )}
      <ul className="m-0 flex list-none flex-col gap-4 p-0">
        {(loading ? [] : (catalogue ?? [])).map((cat) => (
          <PlanCard
            key={cat.id}
            plan={cat}
            current={cat.id === plan.plan}
            offer={offerOf(cat.id)}
            sub={sub}
            busy={action.busy}
            onChoose={(kind) => {
              choose(kind, cat.id)
            }}
          />
        ))}
      </ul>
      {!loading && billing !== null && canCancel(sub) && open === null ? (
        <p className="m-0">
          <Button
            variant="secondary"
            disabled={action.busy}
            onClick={() => {
              choose('cancel', 'free')
            }}
          >
            {t(ACTION_LABEL.cancel)}
          </Button>
        </p>
      ) : null}
    </div>
  )
}
