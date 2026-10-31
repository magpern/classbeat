import { useCallback, useEffect, useState, type FormEvent } from 'react'
import {
  adminError,
  deleteUser,
  disableUser,
  dollars,
  enableUser,
  getUser,
  grantEntitlement,
  revokeEntitlement,
  type UserDetail as Detail,
} from '../../api/admin'
import { SelectField } from '../../components/classes/fields'
import { Field, Notice, Section, useAction } from '../../components/forms'
import { Button } from '../../components/ui/button'
import { Chip } from '../../components/ui/chip'
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '../../components/ui/dialog'
import { Link, useRouter } from '../../router/Router'
import { dayOf, endOfDay, FEATURES, PLANS, sourceLabel } from './format'
import { isReason, useAdminWrite, useMfaRequired } from './StepUp'

const CELL = 'border-b border-line py-2 pr-4'

/**
 * One user (ADR 0011 section 6): plan and usage, entitlements with grant and revoke, the credit
 * ledger of the last three periods, connection status only, disable or enable, and delete with
 * the email typed to confirm. Disable and delete are not offered for an admin (the server
 * refuses them with 409 `target_is_admin` anyway). Refunds come with AD3 (#221).
 */
export function UserDetail({ userId }: { userId: string }) {
  const onMfaRequired = useMfaRequired()
  const write = useAdminWrite()
  const action = useAction()
  const [user, setUser] = useState<Detail | null>(null)
  const [missing, setMissing] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [round, setRound] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    getUser(userId, controller.signal).then(
      (found) => {
        setLoadError(null)
        setUser(found)
      },
      (caught: unknown) => {
        if (controller.signal.aborted) return
        if (isReason(caught, 403, 'mfa_required')) onMfaRequired()
        else if (isReason(caught, 404, 'not_found')) setMissing(true)
        else setLoadError(adminError(caught))
      },
    )
    return () => {
      controller.abort()
    }
  }, [userId, round, onMfaRequired])

  const reload = useCallback(() => {
    setRound((n) => n + 1)
  }, [])

  const change = (task: () => Promise<unknown>) => {
    void action.run(() => write(task), adminError).then(reload)
  }

  if (missing) {
    return (
      <p>
        There is no such user. <Link to="/admin/users">Back to users</Link>
      </p>
    )
  }
  if (user === null) return loadError !== null ? <Notice kind="error">{loadError}</Notice> : <p>Loading</p>

  const isAdmin = user.role === 'admin'
  return (
    <div className="flex flex-col gap-4">
      <p className="m-0">
        <Link to="/admin/users">All users</Link>
      </p>
      <Section title={user.email}>
        <p>
          {isAdmin ? <Chip>admin</Chip> : null}{' '}
          {user.disabledAt !== null ? <Chip tone="bad">disabled since {dayOf(user.disabledAt)}</Chip> : null}
        </p>
        <p data-testid="admin-user-plan">
          Plan: <strong>{user.plan.name}</strong>
          {sourceLabel(user.plan.source)}
          {user.plan.endsAt !== null ? `, until ${dayOf(user.plan.endsAt)}` : ''}
          {user.features.length > 0 ? `. Features: ${user.features.join(', ')}` : ''}
        </p>
        <p>
          {user.classesHeld} of {user.classesLimit} classes this month, {dollars(user.costMicros)} model cost. Joined{' '}
          {dayOf(user.createdAt)}.
        </p>
        {action.error !== null ? <Notice kind="error">{action.error}</Notice> : null}
        {isAdmin ? (
          <p className="text-muted">Admins can only be disabled or deleted from the command line.</p>
        ) : (
          <div className="flex flex-wrap gap-2">
            {user.disabledAt === null ? (
              <Button
                variant="secondary"
                disabled={action.busy}
                onClick={() => {
                  change(() => disableUser(user.id))
                }}
              >
                Disable account
              </Button>
            ) : (
              <Button
                variant="secondary"
                disabled={action.busy}
                onClick={() => {
                  change(() => enableUser(user.id))
                }}
              >
                Enable account
              </Button>
            )}
            <DeleteUser email={user.email} userId={user.id} />
          </div>
        )}
      </Section>
      <Section title="Entitlements">
        <Grant userId={user.id} onGranted={reload} />
        {user.entitlements.length === 0 ? <p className="text-muted">None.</p> : null}
        <ul className="m-0 flex list-none flex-col gap-2 p-0">
          {user.entitlements.map((item) => (
            <li key={item.id} className="flex flex-wrap items-center gap-2">
              <Chip tone={item.active ? 'ok' : 'neutral'}>{item.active ? 'active' : item.revokedAt !== null ? 'revoked' : 'inactive'}</Chip>
              <span>
                {item.planId ?? 'features only'}
                {item.features.length > 0 ? ` + ${item.features.join(', ')}` : ''} ({item.source}), from{' '}
                {dayOf(item.startsAt)}
                {item.endsAt !== null ? ` to ${dayOf(item.endsAt)}` : ', no end'}
                {item.note !== null ? `: ${item.note}` : ''}
              </span>
              {item.revokedAt === null ? (
                <Button
                  variant="ghost"
                  disabled={action.busy}
                  onClick={() => {
                    change(() => revokeEntitlement(user.id, item.id))
                  }}
                >
                  Revoke
                </Button>
              ) : null}
            </li>
          ))}
        </ul>
      </Section>
      <Section title="Credits, last three months">
        {user.ledger.length === 0 ? (
          <p className="text-muted">No classes in these months.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-left text-[0.9375rem]">
              <thead className="text-muted">
                <tr>
                  <th className={`${CELL} font-medium`}>Month</th>
                  <th className={`${CELL} font-medium`}>Plan</th>
                  <th className={`${CELL} font-medium`}>State</th>
                  <th className={`${CELL} font-medium`}>Started</th>
                </tr>
              </thead>
              <tbody>
                {user.ledger.map((row) => (
                  <tr key={row.id}>
                    <td className={CELL}>{row.period.slice(0, 7)}</td>
                    <td className={CELL}>{row.planId}</td>
                    <td className={CELL}>
                      {row.state}
                      {row.reason !== null ? ` (${row.reason})` : ''}
                    </td>
                    <td className={CELL}>{dayOf(row.createdAt)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>
      <Section title="Connections">
        <ul className="m-0 flex list-none flex-col gap-1 p-0">
          {user.connections.map((item) => (
            <li key={item.provider}>
              <strong>{item.provider}</strong>: {item.status.replace(/_/g, ' ')}
              {item.lastError !== null ? `, last error ${item.lastError}` : ''}
              {item.lastUsedAt !== null ? `, last used ${dayOf(item.lastUsedAt)}` : ''}
            </li>
          ))}
        </ul>
      </Section>
    </div>
  )
}

function Grant({ userId, onGranted }: { userId: string; onGranted: () => void }) {
  const write = useAdminWrite()
  const action = useAction()
  const [plan, setPlan] = useState<string>(PLANS[0].id)
  const [features, setFeatures] = useState<string[]>([])
  const [ends, setEnds] = useState('')
  const [note, setNote] = useState('')

  const submit = (event: FormEvent) => {
    event.preventDefault()
    const planId = plan === '' ? null : plan
    if (planId === null && features.length === 0) return
    const endsAt = ends === '' ? null : endOfDay(ends)
    void action
      .run(
        () =>
          write(() =>
            grantEntitlement(userId, { planId, features, endsAt, note: note.trim() === '' ? null : note.trim() }),
          ),
        adminError,
      )
      .then((granted) => {
        if (granted !== undefined) {
          setNote('')
          setEnds('')
          setFeatures([])
          onGranted()
        }
      })
  }

  return (
    <form onSubmit={submit} className="mb-4 flex flex-col gap-2" aria-label="Grant">
      {action.error !== null ? <Notice kind="error">{action.error}</Notice> : null}
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
        label="Ends on (optional)"
        type="date"
        value={ends}
        onChange={(e) => {
          setEnds(e.target.value)
        }}
      />
      <Field
        label="Note (optional, admins only)"
        maxLength={200}
        value={note}
        onChange={(e) => {
          setNote(e.target.value)
        }}
      />
      <div>
        <Button type="submit" disabled={action.busy || (plan === '' && features.length === 0)}>
          Grant
        </Button>
      </div>
    </form>
  )
}

/** Delete with the account's email typed to confirm. The typed text goes only in the request body. */
function DeleteUser({ userId, email }: { userId: string; email: string }) {
  const write = useAdminWrite()
  const action = useAction()
  const { navigate } = useRouter()
  const [open, setOpen] = useState(false)
  const [typed, setTyped] = useState('')
  const matches = typed.trim().toLowerCase() === email.trim().toLowerCase()

  const close = (next: boolean) => {
    setOpen(next)
    if (!next) {
      setTyped('')
      action.clear()
    }
  }

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!matches) return
    const confirmation = typed
    setTyped('')
    void action
      .run(async () => {
        await write(() => deleteUser(userId, confirmation))
        return true
      }, adminError)
      .then((deleted) => {
        if (deleted === true) navigate('/admin/users')
      })
  }

  return (
    <Dialog open={open} onOpenChange={close}>
      <Button
        variant="danger"
        onClick={() => {
          close(true)
        }}
      >
        Delete account
      </Button>
      {open ? (
        <DialogContent aria-describedby="delete-user-description">
          <DialogTitle>Delete this account?</DialogTitle>
          <DialogDescription id="delete-user-description">
            This deletes the account and everything it owns: classes, plans and connections. It cannot be undone.
            Type the account&rsquo;s email to confirm.
          </DialogDescription>
          {action.error !== null ? <Notice kind="error">{action.error}</Notice> : null}
          <form onSubmit={submit}>
            <Field
              label="Email"
              type="email"
              autoComplete="off"
              spellCheck={false}
              value={typed}
              onChange={(e) => {
                setTyped(e.target.value)
              }}
            />
            <Button type="submit" variant="danger" disabled={!matches || action.busy}>
              Delete for good
            </Button>
          </form>
        </DialogContent>
      ) : null}
    </Dialog>
  )
}
