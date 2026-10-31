import { useCallback, useEffect, useState, type FormEvent } from 'react'
import {
  adminError,
  createCode,
  createInvite,
  disableCode,
  disableInvite,
  listCodes,
  listInvites,
  type Invite,
  type Person,
  type RedeemCode,
} from '../../api/admin'
import { SelectField } from '../../components/classes/fields'
import { Field, Notice, Section, useAction } from '../../components/forms'
import { Button } from '../../components/ui/button'
import { Chip } from '../../components/ui/chip'
import { dayOf, endOfDay, FEATURES, PLANS } from './format'
import { isReason, useAdminWrite, useMfaRequired } from './StepUp'

const CELL = 'border-b border-line py-2 pr-4'

interface Shown {
  what: 'invite' | 'redeem code'
  code: string
}

function person(value: Person | null, none: string): string {
  if (value === null) return none
  return value.email ?? 'a deleted account'
}

function stateTone(state: string): 'ok' | 'neutral' | 'bad' {
  if (state === 'active') return 'ok'
  if (state === 'disabled') return 'bad'
  return 'neutral'
}

/**
 * Codes (ADR 0011 section 6): invites and redeem codes. A new code comes back once, in the create
 * response; it is shown here with a copy button until the admin closes it or leaves the page, and
 * is never kept anywhere else (no storage, no URL, not in the lists, which never carry it).
 */
export function Codes() {
  const [shown, setShown] = useState<Shown | null>(null)
  return (
    <div className="flex flex-col gap-4">
      {shown !== null ? (
        <OneTimeCode
          shown={shown}
          onDone={() => {
            setShown(null)
          }}
        />
      ) : null}
      <Invites onCreated={(code) => {
        setShown({ what: 'invite', code })
      }} />
      <RedeemCodes onCreated={(code) => {
        setShown({ what: 'redeem code', code })
      }} />
    </div>
  )
}

function OneTimeCode({ shown, onDone }: { shown: Shown; onDone: () => void }) {
  const [copied, setCopied] = useState<boolean | null>(null)
  const copy = () => {
    const clipboard = typeof navigator === 'undefined' ? undefined : navigator.clipboard
    if (clipboard === undefined) {
      setCopied(false)
      return
    }
    clipboard.writeText(shown.code).then(
      () => {
        setCopied(true)
      },
      () => {
        setCopied(false)
      },
    )
  }
  return (
    <section aria-label="New code" className="rounded-lg border border-brand bg-surface p-5" data-testid="one-time-code">
      <h2 className="m-0 mb-2 font-display text-xl font-bold">Your new {shown.what}</h2>
      <p className="m-0 mb-3 text-muted">Copy it now. It is shown only this once and cannot be shown again.</p>
      <p className="m-0 mb-3 font-mono text-lg break-all select-all">{shown.code}</p>
      <div className="flex flex-wrap items-center gap-2">
        <Button onClick={copy}>Copy</Button>
        <Button variant="secondary" onClick={onDone}>
          Done
        </Button>
        {copied === true ? <span role="status">Copied.</span> : null}
        {copied === false ? <span role="status">Copy it by hand: the browser did not allow copying.</span> : null}
      </div>
    </section>
  )
}

function useList<T>(load: (offset: number, signal: AbortSignal) => Promise<{ items: T[]; nextOffset: number | null }>) {
  const onMfaRequired = useMfaRequired()
  const [items, setItems] = useState<T[] | null>(null)
  const [nextOffset, setNextOffset] = useState<number | null>(null)
  const [offset, setOffset] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const [round, setRound] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    load(offset, controller.signal).then(
      (found) => {
        setError(null)
        setItems(found.items)
        setNextOffset(found.nextOffset)
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
  }, [load, offset, round, onMfaRequired])

  const reload = useCallback(() => {
    setRound((n) => n + 1)
  }, [])
  return { items, nextOffset, offset, setOffset, error, reload }
}

const loadInvites = (offset: number, signal: AbortSignal) =>
  listInvites(offset, signal).then((page) => ({ items: page.invites, nextOffset: page.nextOffset }))
const loadCodes = (offset: number, signal: AbortSignal) =>
  listCodes(offset, signal).then((page) => ({ items: page.codes, nextOffset: page.nextOffset }))

function Pager({ offset, nextOffset, setOffset }: { offset: number; nextOffset: number | null; setOffset: (n: number) => void }) {
  return (
    <div className="mt-2 flex gap-2">
      {offset > 0 ? (
        <Button
          variant="secondary"
          onClick={() => {
            setOffset(0)
          }}
        >
          Newest
        </Button>
      ) : null}
      {nextOffset !== null ? (
        <Button
          variant="secondary"
          onClick={() => {
            setOffset(nextOffset)
          }}
        >
          Older
        </Button>
      ) : null}
    </div>
  )
}

function Invites({ onCreated }: { onCreated: (code: string) => void }) {
  const write = useAdminWrite()
  const action = useAction()
  const list = useList<Invite>(loadInvites)
  const [days, setDays] = useState('7')

  const create = (event: FormEvent) => {
    event.preventDefault()
    const value = Number(days)
    if (!Number.isInteger(value) || value < 1 || value > 365) return
    void action.run(() => write(() => createInvite(value)), adminError).then((made) => {
      if (made !== undefined) {
        onCreated(made.code)
        list.reload()
      }
    })
  }

  const disable = (id: string) => {
    void action.run(() => write(() => disableInvite(id)), adminError).then(list.reload)
  }

  return (
    <Section title="Invites">
      {action.error !== null ? <Notice kind="error">{action.error}</Notice> : null}
      <form onSubmit={create} className="mb-4 flex flex-wrap items-end gap-2" aria-label="Create an invite">
        <Field
          label="Valid for days"
          type="number"
          min={1}
          max={365}
          value={days}
          onChange={(e) => {
            setDays(e.target.value)
          }}
        />
        <Button type="submit" disabled={action.busy}>
          Create invite
        </Button>
      </form>
      {list.error !== null ? <Notice kind="error">{list.error}</Notice> : null}
      {list.items === null && list.error === null ? <p>Loading</p> : null}
      {list.items !== null && list.items.length === 0 ? <p className="text-muted">No invites yet.</p> : null}
      {list.items !== null && list.items.length > 0 ? (
        <div className="relative overflow-x-auto" tabIndex={0} role="region" aria-label="Invites">
          <table className="w-full border-collapse text-left text-[0.9375rem]">
            <thead className="text-muted">
              <tr>
                <th className={`${CELL} font-medium`}>State</th>
                <th className={`${CELL} font-medium`}>Created by</th>
                <th className={`${CELL} font-medium`}>Used by</th>
                <th className={`${CELL} font-medium`}>Expires</th>
                <th className={`${CELL} font-medium`}>
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {list.items.map((invite) => (
                <tr key={invite.id}>
                  <td className={CELL}>
                    <Chip tone={stateTone(invite.state)}>{invite.state}</Chip>
                  </td>
                  <td className={CELL}>{person(invite.createdBy, 'the command line')}</td>
                  <td className={CELL}>
                    {person(invite.usedBy, '')}
                    {invite.usedAt !== null ? ` on ${dayOf(invite.usedAt)}` : ''}
                  </td>
                  <td className={CELL}>{dayOf(invite.expiresAt)}</td>
                  <td className={CELL}>
                    {invite.state === 'active' ? (
                      <Button
                        variant="ghost"
                        disabled={action.busy}
                        onClick={() => {
                          disable(invite.id)
                        }}
                      >
                        Disable
                      </Button>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      <Pager offset={list.offset} nextOffset={list.nextOffset} setOffset={list.setOffset} />
    </Section>
  )
}

function RedeemCodes({ onCreated }: { onCreated: (code: string) => void }) {
  const write = useAdminWrite()
  const action = useAction()
  const list = useList<RedeemCode>(loadCodes)
  const [label, setLabel] = useState('')
  const [plan, setPlan] = useState<string>(PLANS[0].id)
  const [features, setFeatures] = useState<string[]>([])
  const [grantDays, setGrantDays] = useState('30')
  const [uses, setUses] = useState('10')
  const [expires, setExpires] = useState('')

  const valid =
    label.trim() !== '' &&
    (plan !== '' || features.length > 0) &&
    endOfDay(expires) !== null &&
    Number.isInteger(Number(grantDays)) &&
    Number.isInteger(Number(uses))

  const create = (event: FormEvent) => {
    event.preventDefault()
    const expiresAt = endOfDay(expires)
    if (!valid || expiresAt === null) return
    const input = {
      label: label.trim(),
      planId: plan === '' ? null : plan,
      features,
      grantDays: Number(grantDays),
      maxRedemptions: Number(uses),
      expiresAt,
    }
    void action.run(() => write(() => createCode(input)), adminError).then((made) => {
      if (made !== undefined) {
        setLabel('')
        onCreated(made.code)
        list.reload()
      }
    })
  }

  const disable = (id: string) => {
    void action.run(() => write(() => disableCode(id)), adminError).then(list.reload)
  }

  return (
    <Section title="Redeem codes">
      {action.error !== null ? <Notice kind="error">{action.error}</Notice> : null}
      <form onSubmit={create} className="mb-4 flex flex-col gap-2" aria-label="Create a redeem code">
        <Field
          label="Label"
          maxLength={80}
          value={label}
          onChange={(e) => {
            setLabel(e.target.value)
          }}
        />
        <SelectField
          label="Plan"
          value={plan}
          onChange={(e) => {
            setPlan(e.target.value)
          }}
        >
          {PLANS.map((item) => (
            <option key={item.id} value={item.id}>
              {item.name}
            </option>
          ))}
          <option value="">No plan (features only)</option>
        </SelectField>
        {FEATURES.map((item) => (
          <label key={item.id} className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={features.includes(item.id)}
              onChange={(e) => {
                const on = e.target.checked
                setFeatures((now) => (on ? [...now, item.id] : now.filter((f) => f !== item.id)))
              }}
            />
            {item.name}
          </label>
        ))}
        <Field
          label="Days it grants"
          type="number"
          min={1}
          max={366}
          value={grantDays}
          onChange={(e) => {
            setGrantDays(e.target.value)
          }}
        />
        <Field
          label="How many people can use it"
          type="number"
          min={1}
          max={1000}
          value={uses}
          onChange={(e) => {
            setUses(e.target.value)
          }}
        />
        <Field
          label="Can be redeemed until"
          type="date"
          value={expires}
          onChange={(e) => {
            setExpires(e.target.value)
          }}
        />
        <div>
          <Button type="submit" disabled={action.busy || !valid}>
            Create redeem code
          </Button>
        </div>
      </form>
      {list.error !== null ? <Notice kind="error">{list.error}</Notice> : null}
      {list.items === null && list.error === null ? <p>Loading</p> : null}
      {list.items !== null && list.items.length === 0 ? <p className="text-muted">No redeem codes yet.</p> : null}
      {list.items !== null && list.items.length > 0 ? (
        <div className="relative overflow-x-auto" tabIndex={0} role="region" aria-label="Redeem codes">
          <table className="w-full border-collapse text-left text-[0.9375rem]">
            <thead className="text-muted">
              <tr>
                <th className={`${CELL} font-medium`}>Label</th>
                <th className={`${CELL} font-medium`}>Grants</th>
                <th className={`${CELL} font-medium`}>Used</th>
                <th className={`${CELL} font-medium`}>Until</th>
                <th className={`${CELL} font-medium`}>State</th>
                <th className={`${CELL} font-medium`}>
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {list.items.map((code) => (
                <tr key={code.id}>
                  <td className={CELL}>{code.label}</td>
                  <td className={CELL}>
                    {code.planId ?? 'features only'}
                    {code.features.length > 0 ? ` + ${code.features.join(', ')}` : ''} for {code.grantDays} days
                  </td>
                  <td className={CELL}>
                    {code.redeemedCount} of {code.maxRedemptions}
                  </td>
                  <td className={CELL}>{dayOf(code.expiresAt)}</td>
                  <td className={CELL}>
                    <Chip tone={stateTone(code.state)}>{code.state.replace(/_/g, ' ')}</Chip>
                  </td>
                  <td className={CELL}>
                    {code.disabledAt === null ? (
                      <Button
                        variant="ghost"
                        disabled={action.busy}
                        onClick={() => {
                          disable(code.id)
                        }}
                      >
                        Disable
                      </Button>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      <Pager offset={list.offset} nextOffset={list.nextOffset} setOffset={list.setOffset} />
    </Section>
  )
}
