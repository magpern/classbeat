import { PlanNudge } from '../plans/PlanNudge'
import { PlanOffers } from '../plans/PlanOffers'
import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { getPlan, redeemCode, startTrial, type Plan as PlanData } from '../../api/plan'
import { accountPlanError, dayText, sourceText } from '../../api/planMessages'
import type { CatalogueKey } from '../../i18n/en'
import { Trans, useT } from '../../i18n/I18n'
import { Field, Notice, Section, useAction } from '../forms'
import { Progress } from '../ui/progress'
import { classesLine } from './classesLine'
import { UsageRing } from './UsageRing'

function planLineKey(source: string | null, end: string | null): CatalogueKey {
  if (source !== null) return end !== null ? 'plan.lineSourceEnd' : 'plan.lineSource'
  return end !== null ? 'plan.lineEnd' : 'plan.line'
}

/**
 * Plan and usage (ADR 0011 section 9): the plan, its source and end, classes used this month,
 * the cost share as a bar (never a dollar figure), a redeem form and, when offered, a trial.
 */
export function Plan({ onChanged }: { onChanged?: () => void }) {
  const t = useT()
  const [plan, setPlan] = useState<PlanData | null>(null)
  const [failed, setFailed] = useState(false)
  const [round, setRound] = useState(0)
  const [code, setCode] = useState('')
  const [done, setDone] = useState<string | null>(null)
  const action = useAction()

  useEffect(() => {
    const abort = new AbortController()
    getPlan(abort.signal).then(
      (found) => {
        setFailed(false)
        setPlan(found)
      },
      () => {
        if (!abort.signal.aborted) setFailed(true)
      },
    )
    return () => {
      abort.abort()
    }
  }, [round])

  const refresh = useCallback(() => {
    setRound((n) => n + 1)
    onChanged?.()
  }, [onChanged])

  const redeem = (event: FormEvent) => {
    event.preventDefault()
    const value = code.trim()
    if (value === '') return
    setDone(null)
    void action
      .run(async () => {
        await redeemCode(value)
        return true
      }, accountPlanError)
      .then((ok) => {
        if (ok === true) {
          setCode('')
          setDone(t('plan.redeemed'))
          refresh()
        }
      })
  }

  const trial = () => {
    setDone(null)
    void action
      .run(async () => {
        await startTrial()
        return true
      }, accountPlanError)
      .then((ok) => {
        if (ok === true) {
          setDone(t('plan.trialStarted'))
          refresh()
        }
      })
  }

  if (plan === null) {
    return (
      <Section title={t('plan.title')}>
        {failed ? (
          <>
            <Notice kind="error">{t('plan.loadFailed')}</Notice>
            <button type="button" className="button" onClick={refresh}>
              {t('common.tryAgain')}
            </button>
          </>
        ) : (
          <p>{t('common.loading')}</p>
        )}
      </Section>
    )
  }
  const source = sourceText(plan.source)
  const end = dayText(plan.endsAt)
  const percent = Math.round(plan.costShare * 100)
  return (
    <Section title={t('plan.title')}>
      {/* A phone: the ring beside the badge, name and classes line, the nudge and budget full width
          below, so the ring never sits alone in a tall row (issue 1277). From sm the ring is
          centred against the whole text column, as before. */}
      <div
        data-testid="plan-card"
        className="grid grid-cols-[auto_minmax(0,1fr)] items-center gap-x-4 gap-y-3 rounded-xl bg-raised p-4 sm:gap-x-6 sm:p-5"
      >
        <UsageRing held={plan.classesHeld} limit={plan.limits.classesPerMonth} />
        <div className="flex min-w-0 flex-col gap-2 sm:col-start-2 sm:row-start-1 sm:gap-3">
          <span className="inline-flex w-fit rounded-full bg-heat px-3 py-1 text-xs font-bold text-on-heat">
            {t('plan.options.current')}
          </span>
          <p data-testid="plan-name">
            <Trans
              id={planLineKey(source, end)}
              params={{ source: source ?? '', end: end ?? '' }}
              slots={{ name: <strong>{plan.name}</strong> }}
            />
          </p>
          <p data-testid="plan-classes">{classesLine(plan)}</p>
        </div>
        <div className="col-span-2 flex min-w-0 flex-col gap-3 sm:col-span-1 sm:col-start-2 sm:row-start-2">
          <PlanNudge trigger="account" plan={plan} />
          <div className="flex flex-col gap-2">
            <Progress
              value={percent}
              aria-label={t('plan.budgetLabel')}
              data-testid="plan-cost-bar"
              className="[&>div]:bg-heat"
            />
            <p className="muted">
              {t('plan.budgetText', { percent, day: dayText(plan.periodEnd) ?? t('plan.firstFallback') })}
            </p>
          </div>
        </div>
      </div>
      <PlanOffers plan={plan} onChanged={refresh} />
      {done !== null ? <Notice kind="success">{done}</Notice> : null}
      {action.error ? <Notice kind="error">{action.error}</Notice> : null}
      <form onSubmit={redeem}>
        <Field
          label={t('plan.redeemLabel')}
          value={code}
          maxLength={64}
          autoComplete="off"
          spellCheck={false}
          onChange={(event) => {
            setCode(event.target.value)
          }}
        />
        <button className="button" type="submit" disabled={action.busy || code.trim() === ''}>
          {t('plan.redeem')}
        </button>
      </form>
      {plan.trial !== null ? (
        <p>
          <button type="button" className="button button--secondary" disabled={action.busy} onClick={trial}>
            {t('plan.trial', { days: plan.trial.days })}
          </button>
        </p>
      ) : null}
    </Section>
  )
}
