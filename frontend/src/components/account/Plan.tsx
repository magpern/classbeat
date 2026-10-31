import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { getPlan, redeemCode, startTrial, type Plan as PlanData } from '../../api/plan'
import { accountPlanError, dayText, periodMonth, sourceText } from '../../api/planMessages'
import { Field, Notice, Section, useAction } from '../forms'
import { Progress } from '../ui/progress'

/** "3 of 30 classes in October": the figure the account page shows. */
export function classesLine(plan: PlanData): string {
  const month = periodMonth(plan.periodEnd) ?? 'this month'
  const limit = plan.limits.classesPerMonth
  return `${String(plan.classesHeld)} of ${String(limit)} ${limit === 1 ? 'class' : 'classes'} in ${month}`
}

/**
 * Plan and usage (ADR 0011 section 9): the plan, its source and end, classes used this month,
 * the cost share as a bar (never a dollar figure), a redeem form and, when offered, a trial.
 */
export function Plan() {
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
  }, [])

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
          setDone('The code is redeemed.')
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
          setDone('Your trial has started.')
          refresh()
        }
      })
  }

  if (plan === null) {
    return (
      <Section title="Plan and usage">
        {failed ? (
          <>
            <Notice kind="error">Your plan could not be loaded.</Notice>
            <button type="button" className="button" onClick={refresh}>
              Try again
            </button>
          </>
        ) : (
          <p>Loading</p>
        )}
      </Section>
    )
  }
  const source = sourceText(plan.source)
  const end = dayText(plan.endsAt)
  const percent = Math.round(plan.costShare * 100)
  return (
    <Section title="Plan and usage">
      <p data-testid="plan-name">
        Your plan: <strong>{plan.name}</strong>
        {source !== null ? ` (${source})` : ''}
        {end !== null ? `, until ${end}` : ''}
      </p>
      <p data-testid="plan-classes">{classesLine(plan)}</p>
      <div>
        <Progress value={percent} aria-label="Share of this month's budget used" data-testid="plan-cost-bar" />
        <p className="muted">
          {percent}% of this month&rsquo;s budget used. It starts over on {dayText(plan.periodEnd) ?? 'the 1st'}.
        </p>
      </div>
      {done !== null ? <Notice kind="success">{done}</Notice> : null}
      {action.error ? <Notice kind="error">{action.error}</Notice> : null}
      <form onSubmit={redeem}>
        <Field
          label="Redeem a code"
          value={code}
          maxLength={64}
          autoComplete="off"
          spellCheck={false}
          onChange={(event) => {
            setCode(event.target.value)
          }}
        />
        <button className="button" type="submit" disabled={action.busy || code.trim() === ''}>
          Redeem
        </button>
      </form>
      {plan.trial !== null ? (
        <p>
          <button type="button" className="button button--secondary" disabled={action.busy} onClick={trial}>
            Start your {String(plan.trial.days)}-day trial
          </button>
        </p>
      ) : null}
    </Section>
  )
}
