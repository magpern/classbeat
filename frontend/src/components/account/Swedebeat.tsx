import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { ApiError } from '../../api/client'
import {
  connectSwedebeat,
  disconnectSwedebeat,
  getConnectionsView,
  type Connection,
  type SwedebeatAccess,
} from '../../api/connections'
import { errorMessage } from '../../api/messages'
import { useGuard } from '../../auth/prompts'
import { Field, Notice, Section, useAction } from '../forms'

// Plain words for the connect failures (ADR 0015 section 7). The raw code is never shown.
const CONNECT_ERRORS: Record<string, string> = {
  invalid_credentials: 'Swedebeat did not accept that email and password.',
  upstream_unavailable: 'Swedebeat could not be reached. Try again in a few minutes.',
  app_key_unavailable: 'Swedebeat sign-in is not available on this server.',
  feature_off: 'Swedebeat is not switched on right now.',
  unknown: 'Swedebeat answered in a way classbeat did not expect. Try again later.',
}

const LAST_ERRORS: Record<string, string> = {
  needs_reconnect: 'Swedebeat no longer accepts the stored sign-in. Connect again.',
  decrypt_failed: 'The stored sign-in could not be read. Connect again.',
  rate_limited: 'Swedebeat asked classbeat to slow down.',
  upstream_unavailable: 'Swedebeat could not be reached the last time.',
}

const DEVICE_IN_USE =
  'Your Swedebeat account is signed in on another device, probably the Swedebeat app on your phone. ' +
  'If you sign in here anyway, that device is signed out of Swedebeat.'

function lookup(table: Record<string, string>, key: string): string | undefined {
  return Object.hasOwn(table, key) ? table[key] : undefined
}

function connectError(error: unknown): string {
  if (error instanceof ApiError && error.status !== 429 && error.reason !== null) {
    const known = lookup(CONNECT_ERRORS, error.reason)
    if (known !== undefined) return known
  }
  return errorMessage(error)
}

function since(iso: string): string {
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleDateString()
}

/**
 * Swedebeat (ADR 0015, ADR 0013): optional, for instructors who teach with Swedebeat music.
 * Off, the card says it is coming soon. On, and the server holds Swedebeat's app key, it shows
 * the connect form (email and password, used once, never kept), then the connected state with the
 * email, and a confirmed disconnect. If another device holds the Swedebeat session, the user is
 * asked before it is signed out.
 */
export function Swedebeat() {
  const guard = useGuard()
  const [view, setView] = useState<{ access: SwedebeatAccess; account: Connection | null } | null>(null)
  const [loadFailed, setLoadFailed] = useState(false)
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [note, setNote] = useState<string | null>(null)
  const [taken, setTaken] = useState(false)
  const [confirmingDisconnect, setConfirmingDisconnect] = useState(false)
  const action = useAction()

  const load = useCallback(async () => {
    const all = await getConnectionsView()
    setView({
      access: all.swedebeat,
      account: all.connections.find((item) => item.provider === 'swedebeat') ?? null,
    })
  }, [])

  useEffect(() => {
    let current = true
    getConnectionsView().then(
      (all) => {
        if (current) {
          setView({
            access: all.swedebeat,
            account: all.connections.find((item) => item.provider === 'swedebeat') ?? null,
          })
        }
      },
      () => {
        if (current) setLoadFailed(true)
      },
    )
    return () => {
      current = false
    }
  }, [])

  const connect = (event: FormEvent) => {
    event.preventDefault()
    setNote(null)
    const sent = { email: email.trim(), password, force: taken }
    void action
      .run(async () => {
        const made = await guard(() => connectSwedebeat(sent.email, sent.password, sent.force))
        setTaken(false)
        setView((was) => (was === null ? was : { ...was, account: made }))
        setNote('Swedebeat is connected.')
      }, (error) => {
        if (error instanceof ApiError && error.reason === 'device_in_use') {
          setTaken(true)
          return DEVICE_IN_USE
        }
        setTaken(false)
        return connectError(error)
      })
      .finally(() => {
        setPassword('') // the password is not kept on the page after the attempt
      })
    setEmail(sent.email)
  }

  const disconnect = () => {
    setNote(null)
    setConfirmingDisconnect(false)
    void action.run(async () => {
      await guard(() => disconnectSwedebeat())
      await load()
      setNote('Swedebeat is disconnected. classbeat no longer holds its sign-in.')
    })
  }

  if (view === null) {
    return (
      <Section title="Swedebeat">
        {loadFailed ? <Notice kind="error">The connections could not be loaded.</Notice> : <p>Loading</p>}
      </Section>
    )
  }

  const { access, account } = view
  const needsForm = access.enabled && access.available && (account === null || account.status === 'needs_reconnect')
  return (
    <Section title="Swedebeat">
      {note ? <Notice kind="success">{note}</Notice> : null}
      {action.error ? <Notice kind="error">{action.error}</Notice> : null}
      {!access.enabled && account === null ? <p className="text-muted">Swedebeat is coming soon.</p> : null}
      {access.enabled && !access.available && account === null ? (
        <p className="text-muted">Swedebeat sign-in is not available on this server.</p>
      ) : null}
      {account ? (
        <ul className="rows">
          <li>
            <span>Status</span>
            <span data-testid="swedebeat-status">
              {account.status === 'connected' ? 'Connected' : 'Needs to be connected again'}
            </span>
          </li>
          {account.account ? (
            <li>
              <span>Swedebeat account</span>
              <span data-testid="swedebeat-account">{account.account}</span>
            </li>
          ) : null}
          <li>
            <span>Connected since</span>
            <span className="muted">{since(account.connectedAt)}</span>
          </li>
          {account.lastError ? (
            <li>
              <span>Last problem</span>
              <span className="muted">
                {lookup(LAST_ERRORS, account.lastError) ?? 'The last use of this connection failed.'}
              </span>
            </li>
          ) : null}
        </ul>
      ) : null}
      {needsForm ? (
        <form onSubmit={connect}>
          <p>
            Swedebeat is optional. Sign in with your Swedebeat account. classbeat uses your password
            once to get a sign-in token and does not keep it.
          </p>
          <Field
            label="Swedebeat email"
            type="email"
            autoComplete="username"
            value={email}
            onChange={(e) => {
              setEmail(e.target.value)
              // A "sign in here anyway" answer was for the account just tried: another email
              // is a new attempt and starts without force.
              setTaken(false)
            }}
            required
          />
          <Field
            label="Swedebeat password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => {
              setPassword(e.target.value)
            }}
            required
          />
          {taken ? (
            <p className="notice" data-kind="info" data-testid="swedebeat-device-warning">
              Enter your password again to sign in here anyway.
            </p>
          ) : null}
          <button className="button" type="submit" disabled={action.busy}>
            {taken
              ? 'Sign in here anyway'
              : account
                ? 'Connect Swedebeat again'
                : 'Connect Swedebeat'}
          </button>
          {taken ? (
            <button
              className="button button--secondary"
              type="button"
              onClick={() => {
                setTaken(false)
                action.clear()
              }}
            >
              Keep the other device signed in
            </button>
          ) : null}
        </form>
      ) : null}
      {account ? (
        <>
          <p className="muted">
            Disconnecting deletes the sign-in classbeat holds. It does not sign you out of the
            Swedebeat app.
          </p>
          {confirmingDisconnect ? (
            <div className="notice" role="group" aria-label="Confirm disconnect Swedebeat">
              <p>classbeat will forget your Swedebeat sign-in. You can connect again at any time.</p>
              <button className="button" type="button" disabled={action.busy} onClick={disconnect}>
                Yes, disconnect Swedebeat
              </button>
              <button
                className="button button--secondary"
                type="button"
                onClick={() => {
                  setConfirmingDisconnect(false)
                }}
              >
                Keep it connected
              </button>
            </div>
          ) : (
            <button
              className="button button--secondary"
              type="button"
              disabled={action.busy}
              onClick={() => {
                setConfirmingDisconnect(true)
              }}
            >
              Disconnect Swedebeat
            </button>
          )}
        </>
      ) : null}
    </Section>
  )
}
