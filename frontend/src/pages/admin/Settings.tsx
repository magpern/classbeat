import { useCallback, useEffect, useState, type FormEvent, type ReactNode } from 'react'
import { adminError, dollars } from '../../api/admin'
import {
  asBanner,
  asText,
  asTrial,
  clearFlag,
  getSettings,
  setFlag,
  type BannerValue,
  type Flag,
  type FlagKey,
  type SettingsView,
  type Trial,
} from '../../api/adminSettings'
import { SelectField, TextAreaField } from '../../components/classes/fields'
import { Field, Notice, Section, useAction } from '../../components/forms'
import { Button } from '../../components/ui/button'
import { Chip } from '../../components/ui/chip'
import { Switch } from '../../components/ui/switch'
import { PlanLimits } from './PlanLimits'
import { isReason, useAdminWrite, useMfaRequired } from './StepUp'
import { SectionTitle, when } from './Table'

// Why the environment forbids a choice: shown next to the disabled control.
export const REASONS = {
  openSignup: 'Open sign-up can only be set on the server (CLASSBEAT_SIGNUP_MODE).',
  songPickingOff:
    'Song picking is switched off on the server (CLASSBEAT_SONG_PICKING). This setting has no effect until the server switches it on.',
  trialsOff: 'Trials are switched off on the server (CLASSBEAT_TRIAL_DAYS_MAX is 0).',
  trialPlan: 'A trial cannot be of a plan above Instructor.',
  billing: 'Billing does not exist yet. It comes with a later milestone and cannot be switched on here.',
  swedebeatOff:
    'Swedebeat is switched off on the server (CLASSBEAT_SWEDEBEAT_ENABLED). This setting has no effect until the server switches it on.',
}

const TRIAL_MAX_PLAN = 'instructor'

function describe(key: string, value: unknown): string {
  if (value === null || value === undefined) return key === 'banner' ? 'no banner' : key === 'trial' ? 'off' : 'not set'
  if (key === 'trial') {
    const trial = asTrial(value)
    return trial === null ? 'not valid' : trial.days === 0 ? 'off' : `${String(trial.days)} days of ${trial.plan}`
  }
  if (key === 'banner') {
    const banner = asBanner(value)
    return banner === null ? 'not valid' : `${banner.level}: ${banner.text}`
  }
  return asText(value) ?? 'not valid'
}

/** A disabled control's reason, tied to it with `aria-describedby`. */
function Reason({ id, children }: { id: string; children: ReactNode }) {
  return (
    <p id={id} className="m-0 text-[0.9375rem] text-muted">
      {children}
    </p>
  )
}

/**
 * Settings and flags (ADR 0011 sections 6 and 7): every flag with its effective value and where
 * it comes from, the plans' limits, and the settings that are read-only here. A choice the
 * environment forbids is shown disabled with the reason; a refused write shows the server's 422
 * reason in words. Every write goes through the step-up.
 */
export function Settings() {
  const onMfaRequired = useMfaRequired()
  const [view, setView] = useState<SettingsView | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [round, setRound] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    getSettings(controller.signal).then(
      (found) => {
        setError(null)
        setView(found)
      },
      (caught: unknown) => {
        if (controller.signal.aborted) return
        if (isReason(caught, 403, 'mfa_required')) onMfaRequired()
        else setError(adminError(caught))
      },
    )
    return () => {
      controller.abort()
    }
  }, [round, onMfaRequired])

  const reload = useCallback(() => {
    setRound((n) => n + 1)
  }, [])

  return (
    <section aria-labelledby="admin-settings-title">
      <SectionTitle id="admin-settings-title">Settings</SectionTitle>
      <p className="m-0 mb-4 text-muted">
        The server&apos;s environment sets the defaults. A setting here can make them stricter, never looser.
      </p>
      {error !== null ? <Notice kind="error">{error}</Notice> : null}
      {view === null && error === null ? <p>Loading</p> : null}
      {view !== null ? (
        <div className="flex flex-col gap-4">
          {view.flags.map((item) => (
            <FlagCard key={item.key} item={item} view={view} onChanged={reload} />
          ))}
          <PlanLimits plans={view.plans} bounds={view.bounds} onChanged={reload} />
          <ReadOnlySettings view={view} />
        </div>
      ) : null}
    </section>
  )
}

const TITLES: Record<string, string> = {
  signup_mode: 'Sign-up',
  song_picking: 'Song picking',
  swedebeat_enabled: 'Swedebeat',
  trial: 'Trial',
  banner: 'Banner',
}

function FlagCard({ item, view, onChanged }: { item: Flag; view: SettingsView; onChanged: () => void }) {
  const write = useAdminWrite()
  const action = useAction()

  const save = (task: () => Promise<Flag>) => {
    void action.run(() => write(task), adminError).then((saved) => {
      if (saved !== undefined) onChanged()
    })
  }
  const key = item.key as FlagKey
  return (
    <Section title={TITLES[item.key] ?? item.key}>
      <p className="m-0 mb-2" data-testid={`flag-${item.key}`}>
        Now: <strong>{describe(item.key, item.effective)}</strong>{' '}
        <Chip tone={item.source === 'database' ? 'ok' : 'neutral'}>
          {item.source === 'database' ? 'set here' : 'from the environment'}
        </Chip>
        {item.key === 'banner' || item.key === 'trial' ? null : (
          <span className="text-muted"> Environment: {describe(item.key, item.environment)}.</span>
        )}
      </p>
      {item.stored !== null && !item.storedValid ? (
        <Notice kind="error">The stored value is not valid, so the stricter default applies. Save a new value.</Notice>
      ) : null}
      {item.updatedAt !== null && item.stored !== null ? (
        <p className="m-0 mb-2 text-[0.9375rem] text-muted">
          Changed {when(item.updatedAt)}
          {item.updatedBy !== null ? ` by ${item.updatedBy.email ?? 'a deleted account'}` : ''}.
        </p>
      ) : null}
      {action.error !== null ? <Notice kind="error">{action.error}</Notice> : null}
      {key === 'signup_mode' ? <SignupForm item={item} busy={action.busy} onSave={(v) => { save(() => setFlag(key, v)) }} /> : null}
      {key === 'song_picking' ? <SongForm item={item} busy={action.busy} onSave={(v) => { save(() => setFlag(key, v)) }} /> : null}
      {key === 'swedebeat_enabled' ? <SwedebeatForm item={item} busy={action.busy} onSave={(v) => { save(() => setFlag(key, v)) }} /> : null}
      {key === 'trial' ? <TrialForm item={item} view={view} busy={action.busy} onSave={(v) => { save(() => setFlag(key, v)) }} /> : null}
      {key === 'banner' ? <BannerForm item={item} busy={action.busy} onSave={(v) => { save(() => setFlag(key, v)) }} /> : null}
      {item.stored !== null ? (
        <Button
          className="mt-2"
          variant="ghost"
          disabled={action.busy}
          onClick={() => {
            save(() => clearFlag(key))
          }}
        >
          Use the environment&apos;s value
        </Button>
      ) : null}
    </Section>
  )
}

function SignupForm({ item, busy, onSave }: { item: Flag; busy: boolean; onSave: (v: string) => void }) {
  const [mode, setMode] = useState(asText(item.stored) ?? 'invite')
  const submit = (event: FormEvent) => {
    event.preventDefault()
    onSave(mode)
  }
  return (
    <form onSubmit={submit} aria-label="Change the sign-up mode" className="flex flex-wrap items-end gap-2">
      <SelectField label="Sign-up mode" value={mode} aria-describedby="reason-open-signup" onChange={(e) => { setMode(e.target.value) }}>
        <option value="invite">Invite only</option>
        <option value="closed">Closed</option>
        <option value="open" disabled>
          Open (server only)
        </option>
      </SelectField>
      <Button type="submit" disabled={busy}>
        Save
      </Button>
      <Reason id="reason-open-signup">{REASONS.openSignup}</Reason>
    </form>
  )
}

function SongForm({ item, busy, onSave }: { item: Flag; busy: boolean; onSave: (v: string) => void }) {
  const [value, setValue] = useState(asText(item.stored) ?? 'granted')
  const forbidden = item.environment === 'off'
  const submit = (event: FormEvent) => {
    event.preventDefault()
    onSave(value)
  }
  return (
    <form onSubmit={submit} aria-label="Change song picking" className="flex flex-wrap items-end gap-2">
      <SelectField
        label="Song picking for"
        value={value}
        disabled={forbidden}
        aria-describedby={forbidden ? 'reason-song-picking' : undefined}
        onChange={(e) => {
          setValue(e.target.value)
        }}
      >
        <option value="off">Nobody</option>
        <option value="granted">Users granted the feature</option>
        <option value="on">Everyone</option>
      </SelectField>
      <Button type="submit" disabled={busy || forbidden}>
        Save
      </Button>
      {forbidden ? <Reason id="reason-song-picking">{REASONS.songPickingOff}</Reason> : null}
    </form>
  )
}

function SwedebeatForm({ item, busy, onSave }: { item: Flag; busy: boolean; onSave: (v: string) => void }) {
  const [value, setValue] = useState(asText(item.stored) ?? 'on')
  const forbidden = item.environment === 'off'
  const submit = (event: FormEvent) => {
    event.preventDefault()
    onSave(value)
  }
  return (
    <form onSubmit={submit} aria-label="Change Swedebeat" className="flex flex-wrap items-end gap-2">
      <SelectField
        label="Swedebeat sign-in for"
        value={value}
        disabled={forbidden}
        aria-describedby={forbidden ? 'reason-swedebeat' : undefined}
        onChange={(e) => {
          setValue(e.target.value)
        }}
      >
        <option value="off">Nobody</option>
        <option value="on">Everyone</option>
      </SelectField>
      <Button type="submit" disabled={busy || forbidden}>
        Save
      </Button>
      {forbidden ? <Reason id="reason-swedebeat">{REASONS.swedebeatOff}</Reason> : null}
    </form>
  )
}

function TrialForm({ item, view, busy, onSave }: { item: Flag; view: SettingsView; busy: boolean; onSave: (v: Trial) => void }) {
  const stored = asTrial(item.stored)
  const [plan, setPlan] = useState(stored?.plan ?? TRIAL_MAX_PLAN)
  const [days, setDays] = useState(String(stored?.days ?? 14))
  const max = view.bounds.trialDays
  const forbidden = max === 0
  const limit = view.plans.find((p) => p.id === TRIAL_MAX_PLAN)?.rank ?? 0
  const submit = (event: FormEvent) => {
    event.preventDefault()
    onSave({ plan, days: Number(days) })
  }
  return (
    <form onSubmit={submit} aria-label="Change the trial" className="flex flex-wrap items-end gap-2">
      <SelectField
        label="Trial plan"
        value={plan}
        disabled={forbidden}
        aria-describedby={forbidden ? 'reason-trials-off' : 'reason-trial-plan'}
        onChange={(e) => {
          setPlan(e.target.value)
        }}
      >
        {view.plans.map((p) => (
          <option key={p.id} value={p.id} disabled={p.rank > limit}>
            {p.name}
            {p.rank > limit ? ' (not for trials)' : ''}
          </option>
        ))}
      </SelectField>
      <Field
        label="Days (0 is off)"
        type="number"
        min={0}
        max={max}
        step={1}
        required
        disabled={forbidden}
        hint={forbidden ? undefined : `At most ${String(max)} (server bound).`}
        value={days}
        onChange={(e) => {
          setDays(e.target.value)
        }}
      />
      <Button type="submit" disabled={busy || forbidden}>
        Save
      </Button>
      {forbidden ? <Reason id="reason-trials-off">{REASONS.trialsOff}</Reason> : <Reason id="reason-trial-plan">{REASONS.trialPlan}</Reason>}
    </form>
  )
}

function BannerForm({ item, busy, onSave }: { item: Flag; busy: boolean; onSave: (v: BannerValue) => void }) {
  const stored = asBanner(item.stored)
  const [words, setWords] = useState(stored?.text ?? '')
  const [level, setLevel] = useState(stored?.level ?? 'info')
  const submit = (event: FormEvent) => {
    event.preventDefault()
    onSave({ text: words, level })
  }
  return (
    <form onSubmit={submit} aria-label="Change the banner" className="flex flex-col gap-2">
      <TextAreaField
        label="Banner text"
        hint="Shown on every page as plain text, at most 280 characters."
        maxLength={280}
        required
        value={words}
        onChange={(e) => {
          setWords(e.target.value)
        }}
      />
      <div className="flex flex-wrap items-end gap-2">
        <SelectField label="Banner level" value={level} onChange={(e) => { setLevel(e.target.value) }}>
          <option value="info">Information</option>
          <option value="warning">Warning</option>
        </SelectField>
        <Button type="submit" disabled={busy}>
          Save
        </Button>
      </div>
    </form>
  )
}

function ReadOnlySettings({ view }: { view: SettingsView }) {
  const r = view.readOnly
  return (
    <Section title="Read-only here">
      <p className="m-0 mb-2 text-muted">These are set on the server and change only with a reviewed change.</p>
      <ul className="m-0 flex list-none flex-col gap-3 p-0">
        <li>
          Cost ceiling per class design: <strong>{dollars(r.agentJobCostMicros)}</strong>; per song-picking class:{' '}
          <strong>{dollars(r.songJobCostMicros)}</strong> (estimates at list prices).
        </li>
        <li className="flex flex-wrap items-center gap-2">
          <Switch id="setting-billing" checked={r.billingEnabled} disabled aria-describedby="reason-billing" />
          <label htmlFor="setting-billing">Billing</label>
          <Reason id="reason-billing">{REASONS.billing}</Reason>
        </li>
      </ul>
    </Section>
  )
}
