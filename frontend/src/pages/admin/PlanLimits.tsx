import { useId, useState, type FormEvent } from 'react'
import { adminError, dollars } from '../../api/admin'
import { updatePlan, type Bounds, type Plan, type PlanLimits as Limits } from '../../api/adminSettings'
import { Field, Notice, Section, useAction } from '../../components/forms'
import { Button } from '../../components/ui/button'
import { Switch } from '../../components/ui/switch'
import { useAdminWrite } from './StepUp'
import { when } from './Table'
import { SAVES_PER_DAY_RANGE, SAVES_PER_HOUR_RANGE, cap, capText, cents, centsText, micros, whole } from './planInput'

function SwitchRow({
  label,
  checked,
  onChange,
}: {
  label: string
  checked: boolean
  onChange: (v: boolean) => void
}) {
  const id = useId()
  return (
    <div className="flex min-h-11 items-center gap-3">
      <Switch id={id} checked={checked} onCheckedChange={onChange} />
      <label htmlFor={id}>{label}</label>
    </div>
  )
}

/**
 * The plans' limits (ADR 0011 section 4): each field at most the server's bound (the table's
 * range under the environment's). Only the limits that changed are sent; the next reservation
 * reads them and credits already held stay as they are.
 */
export function PlanLimits({
  plans,
  bounds,
  onChanged,
}: {
  plans: Plan[]
  bounds: Bounds
  onChanged: () => void
}) {
  return (
    <Section title="Plan limits">
      <p className="m-0 mb-2 text-muted">
        A change applies to the next class a user starts. Classes already started keep their credit.
      </p>
      <div className="flex flex-col gap-4">
        {plans.map((plan) => (
          <PlanForm key={plan.id} plan={plan} bounds={bounds} onChanged={onChanged} />
        ))}
      </div>
    </Section>
  )
}

function PlanForm({ plan, bounds, onChanged }: { plan: Plan; bounds: Bounds; onChanged: () => void }) {
  const write = useAdminWrite()
  const action = useAction()
  const [classes, setClasses] = useState(String(plan.classesPerMonth))
  const [revisions, setRevisions] = useState(String(plan.revisionsPerClass))
  const [jobs, setJobs] = useState(String(plan.concurrentJobs))
  const [cost, setCost] = useState((plan.monthlyCostMicros / 1_000_000).toFixed(2))
  const [minutes, setMinutes] = useState(String(plan.maxClassMinutes))
  const [games, setGames] = useState(plan.gamesAllowed)
  const [races, setRaces] = useState(plan.racesAllowed)
  const [ftpTests, setFtpTests] = useState(plan.ftpTestsAllowed)
  const [monthly, setMonthly] = useState(centsText(plan.monthlyPriceCents))
  const [intro, setIntro] = useState(centsText(plan.introPriceCents))
  const [until, setUntil] = useState(plan.introPriceUntil ?? '')
  const [currency, setCurrency] = useState(plan.currency ?? '')
  const [perHour, setPerHour] = useState(capText(plan.savesPerHour))
  const [perDay, setPerDay] = useState(capText(plan.savesPerDay))
  const [local, setLocal] = useState<string | null>(null)

  const submit = (event: FormEvent) => {
    event.preventDefault()
    const typed = {
      classes: whole(classes),
      revisions: whole(revisions),
      jobs: whole(jobs),
      cost: micros(cost),
      minutes: whole(minutes),
    }
    if (
      typed.classes === null ||
      typed.revisions === null ||
      typed.jobs === null ||
      typed.cost === null ||
      typed.minutes === null
    ) {
      setLocal('Use whole numbers, and dollars with at most two decimals.')
      return
    }
    const monthlyCents = cents(monthly)
    const introCents = cents(intro)
    if (monthlyCents === undefined || introCents === undefined) {
      setLocal('Prices are amounts with at most two decimals, or empty for none.')
      return
    }
    const code = currency.trim().toUpperCase()
    if (code !== '' && !/^[A-Z]{3}$/.test(code)) {
      setLocal('The currency is a three-letter code such as USD or SEK, or empty for none.')
      return
    }
    const hourCap = cap(perHour, ...SAVES_PER_HOUR_RANGE)
    const dayCap = cap(perDay, ...SAVES_PER_DAY_RANGE)
    if (hourCap === undefined || dayCap === undefined) {
      setLocal('Saves per hour are 1 to 1000 and saves per day 1 to 10000, or empty for the server setting.')
      return
    }
    const until_ = until.trim() === '' ? null : until.trim()
    setLocal(null)
    const changed: Limits = {}
    if (typed.classes !== plan.classesPerMonth) changed.classesPerMonth = typed.classes
    if (typed.revisions !== plan.revisionsPerClass) changed.revisionsPerClass = typed.revisions
    if (typed.jobs !== plan.concurrentJobs) changed.concurrentJobs = typed.jobs
    if (typed.cost !== plan.monthlyCostMicros) changed.monthlyCostMicros = typed.cost
    if (typed.minutes !== plan.maxClassMinutes) changed.maxClassMinutes = typed.minutes
    if (games !== plan.gamesAllowed) changed.gamesAllowed = games
    if (races !== plan.racesAllowed) changed.racesAllowed = races
    if (ftpTests !== plan.ftpTestsAllowed) changed.ftpTestsAllowed = ftpTests
    if (monthlyCents !== plan.monthlyPriceCents) changed.monthlyPriceCents = monthlyCents
    if (introCents !== plan.introPriceCents) changed.introPriceCents = introCents
    if (until_ !== plan.introPriceUntil) changed.introPriceUntil = until_
    if ((code === '' ? null : code) !== plan.currency) changed.currency = code === '' ? null : code
    if (hourCap !== plan.savesPerHour) changed.savesPerHour = hourCap
    if (dayCap !== plan.savesPerDay) changed.savesPerDay = dayCap
    if (Object.keys(changed).length === 0) {
      setLocal('Nothing was changed.')
      return
    }
    void action
      .run(() => write(() => updatePlan(plan.id, changed)), adminError)
      .then((saved) => {
        if (saved !== undefined) onChanged()
      })
  }

  return (
    <form onSubmit={submit} aria-label={`${plan.name} limits`} className="flex flex-col gap-2">
      <h3 className="m-0 font-display text-lg font-bold">{plan.name}</h3>
      {local !== null ? <Notice kind="error">{local}</Notice> : null}
      {action.error !== null ? <Notice kind="error">{action.error}</Notice> : null}
      <div className="flex flex-wrap items-end gap-2">
        <Field
          label="Classes per month"
          inputMode="numeric"
          hint={`At most ${String(bounds.classesPerMonth)}.`}
          value={classes}
          onChange={(e) => {
            setClasses(e.target.value)
          }}
        />
        <Field
          label="Revisions per class"
          inputMode="numeric"
          hint={`At most ${String(bounds.revisionsPerClass)}.`}
          value={revisions}
          onChange={(e) => {
            setRevisions(e.target.value)
          }}
        />
        <Field
          label="Classes at a time"
          inputMode="numeric"
          hint={`1 to ${String(bounds.concurrentJobs)}.`}
          value={jobs}
          onChange={(e) => {
            setJobs(e.target.value)
          }}
        />
        <Field
          label="Monthly cost cap (US dollars, estimate)"
          inputMode="decimal"
          hint={`At most ${dollars(bounds.monthlyCostMicros)}.`}
          value={cost}
          onChange={(e) => {
            setCost(e.target.value)
          }}
        />
        <Field
          label="Longest class (minutes)"
          inputMode="numeric"
          hint={`10 to ${String(bounds.maxClassMinutes)}.`}
          value={minutes}
          onChange={(e) => {
            setMinutes(e.target.value)
          }}
        />
      </div>
      <fieldset className="m-0 flex flex-wrap gap-x-6 border-0 p-0">
        <legend className="mb-1 p-0 font-medium">Included in this plan</legend>
        <SwitchRow label="Games" checked={games} onChange={setGames} />
        <SwitchRow label="Races" checked={races} onChange={setRaces} />
        <SwitchRow label="FTP tests" checked={ftpTests} onChange={setFtpTests} />
      </fieldset>
      <fieldset className="m-0 flex flex-col gap-1 border-0 p-0">
        <legend className="mb-1 p-0 font-medium">Prices</legend>
        <p className="m-0 text-[0.875rem] text-muted">
          Only shown here. Users see no price while billing is off. Leave a price empty for none.
        </p>
        <div className="flex flex-wrap items-end gap-2">
          <Field
            label="Monthly price"
            inputMode="decimal"
            value={monthly}
            onChange={(e) => {
              setMonthly(e.target.value)
            }}
          />
          <Field
            label="Introduction price"
            inputMode="decimal"
            value={intro}
            onChange={(e) => {
              setIntro(e.target.value)
            }}
          />
          <Field
            label="Introduction price ends"
            type="date"
            min="2020-01-01"
            max="2100-12-31"
            value={until}
            onChange={(e) => {
              setUntil(e.target.value)
            }}
          />
          <Field
            label="Currency"
            hint="Three letters, such as USD."
            maxLength={3}
            value={currency}
            onChange={(e) => {
              setCurrency(e.target.value)
            }}
          />
        </div>
      </fieldset>
      <fieldset className="m-0 flex flex-col gap-1 border-0 p-0">
        <legend className="mb-1 p-0 font-medium">Saves to MOWL</legend>
        <p className="m-0 text-[0.875rem] text-muted">
          Save and Update together, per user. Leave empty for the server setting. A larger number than the server
          setting has no effect.
        </p>
        <div className="flex flex-wrap items-end gap-2">
          <Field
            label="Saves per hour"
            inputMode="numeric"
            hint={`Server setting: ${String(bounds.savesPerHour)}.`}
            value={perHour}
            onChange={(e) => {
              setPerHour(e.target.value)
            }}
          />
          <Field
            label="Saves per day"
            inputMode="numeric"
            hint={`Server setting: ${String(bounds.savesPerDay)}.`}
            value={perDay}
            onChange={(e) => {
              setPerDay(e.target.value)
            }}
          />
        </div>
      </fieldset>
      <div>
        <Button type="submit" disabled={action.busy}>
          Save {plan.name}
        </Button>
      </div>
      <p className="m-0 text-[0.875rem] text-muted">
        Changed {when(plan.updatedAt)}
        {plan.updatedBy !== null ? ` by ${plan.updatedBy.email ?? 'a deleted account'}` : ''}.
      </p>
    </form>
  )
}
