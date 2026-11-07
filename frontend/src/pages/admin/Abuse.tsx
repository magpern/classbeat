import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { adminError, disableUser } from '../../api/admin'
import { ABUSE_WINDOWS, getAbuse, type Abuse as AbuseData, type Subject } from '../../api/adminAbuse'
import { SelectField } from '../../components/classes/fields'
import { Notice, useAction } from '../../components/forms'
import { Button } from '../../components/ui/button'
import { Chip } from '../../components/ui/chip'
import { Link, useQuery, useRouter } from '../../router/Router'
import { isReason, useAdminWrite, useMfaRequired } from './StepUp'
import { DataTable, Figure, SectionTitle, SubTitle, when } from './Table'

function windowOf(value: string | null): number {
  const minutes = Number(value)
  return (ABUSE_WINDOWS as readonly number[]).includes(minutes) ? minutes : 60
}

const MORE = 'More rows matched than are listed; the busiest are shown.'

/**
 * Abuse (#632 item 3), read only except for one shortcut: over the last 15, 60 or 240 minutes of
 * the security log, the users with the most requests, the users who hit the rate limit and the
 * sessions seen from several networks. "Disable account" is the existing disable action
 * (it signs the user out everywhere), with its step-up and audit row; it is not offered for an
 * admin or an account that is already disabled. No address, hash or session id is shown: the
 * server does not send any.
 */
export function Abuse() {
  const onMfaRequired = useMfaRequired()
  const write = useAdminWrite()
  const action = useAction()
  const { pathname, navigate } = useRouter()
  const minutes = windowOf(useQuery().get('minutes'))
  const [round, setRound] = useState(0)
  // What was loaded, for the window it belongs to: another window shows "Loading" until its answer.
  const [loaded, setLoaded] = useState<{ minutes: number; data: AbuseData | null; error: string | null }>({
    minutes: 0,
    data: null,
    error: null,
  })
  const data = loaded.minutes === minutes ? loaded.data : null
  const error = loaded.minutes === minutes ? loaded.error : null

  useEffect(() => {
    const controller = new AbortController()
    getAbuse(minutes, controller.signal).then(
      (found) => {
        setLoaded({ minutes, data: found, error: null })
      },
      (caught: unknown) => {
        if (controller.signal.aborted) return
        if (isReason(caught, 403, 'mfa_required')) onMfaRequired()
        else setLoaded({ minutes, data: null, error: adminError(caught) })
      },
    )
    return () => {
      controller.abort()
    }
  }, [minutes, round, onMfaRequired])

  const disable = useCallback(
    (id: string) => {
      void action.run(() => write(() => disableUser(id)), adminError).then(() => {
        setRound((n) => n + 1)
      })
    },
    [action, write],
  )

  const who = (s: Subject): ReactNode => (
    <span key="who">
      <Link to={`/admin/users/${s.userId}`}>{s.email ?? 'Deleted account'}</Link>
      {s.role === 'admin' ? <Chip>admin</Chip> : null}
      {s.disabled ? <Chip tone="bad">disabled</Chip> : null}
    </span>
  )
  const shortcut = (s: Subject): ReactNode =>
    s.role === 'admin' || s.disabled || s.role === null ? (
      ''
    ) : (
      <Button
        key="disable"
        variant="secondary"
        disabled={action.busy}
        onClick={() => {
          disable(s.userId)
        }}
      >
        Disable account
        <span className="sr-only"> {s.email ?? ''}, signs them out everywhere</span>
      </Button>
    )

  return (
    <section aria-labelledby="admin-abuse-title">
      <SectionTitle id="admin-abuse-title">Abuse</SectionTitle>
      <form className="mb-4 flex flex-wrap items-end gap-2" onSubmit={(e) => { e.preventDefault() }}>
        <SelectField
          label="Window"
          value={String(minutes)}
          onChange={(e) => {
            navigate(`${pathname}?minutes=${e.target.value}`)
          }}
        >
          {ABUSE_WINDOWS.map((m) => (
            <option key={m} value={String(m)}>
              last {m} minutes
            </option>
          ))}
        </SelectField>
      </form>
      <p className="m-0 mb-2 text-muted">
        From the security log: a coarse network is a hashed address bucket, never shown. A rate-limit hit is a
        minute in which the user got a 429.
      </p>
      {error !== null ? <Notice kind="error">{error}</Notice> : null}
      {action.error !== null ? <Notice kind="error">{action.error}</Notice> : null}
      {data === null && error === null ? <p>Loading</p> : null}
      {data !== null ? (
        <>
          <dl className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-3">
            <Figure label="Requests" value={data.requests} />
            <Figure label="Rate-limited" value={data.rateLimited} />
            <Figure label="Users seen" value={data.users} />
          </dl>

          <SubTitle>Top users by request rate</SubTitle>
          {data.topUsersTruncated ? <p className="m-0 mb-2 text-muted">{MORE}</p> : null}
          <DataTable
            caption="Top users by request rate"
            head={['User', 'Requests', 'Per minute', 'Busiest minute', 'Rate-limited', 'Networks', 'Sessions', '']}
            empty="No requests in this window."
            rows={data.topUsers.map((u) => ({
              key: u.userId,
              cells: [who(u), u.requests, u.perMinute, u.busiestMinute, u.rateLimited, u.networks, u.sessions, shortcut(u)],
            }))}
          />

          <SubTitle>Rate-limit hits</SubTitle>
          {data.limitHitsTruncated ? <p className="m-0 mb-2 text-muted">{MORE}</p> : null}
          <DataTable
            caption="Users who hit the rate limit"
            head={['User', 'Minutes limited', 'First', 'Last', '']}
            empty="Nobody was rate-limited in this window."
            rows={data.limitHits.map((h) => ({
              key: h.userId,
              cells: [who(h), h.hits, when(h.firstAt), when(h.lastAt), shortcut(h)],
            }))}
          />

          <SubTitle>Sessions seen from several networks</SubTitle>
          {data.movedSessionsTruncated ? <p className="m-0 mb-2 text-muted">{MORE}</p> : null}
          <DataTable
            caption="Sessions seen from several networks"
            head={['User', 'Networks', 'Requests', 'First', 'Last', '']}
            empty="No session came from more than one network."
            rows={data.movedSessions.map((m, index) => ({
              key: `${m.userId}-${String(index)}`,
              cells: [who(m), m.networks, m.requests, when(m.firstAt), when(m.lastAt), shortcut(m)],
            }))}
          />
        </>
      ) : null}
    </section>
  )
}
