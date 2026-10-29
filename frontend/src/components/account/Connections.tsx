import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { ApiError } from '../../api/client'
import {
  connectMowl,
  disconnectMowl,
  getConnections,
  type Connection,
} from '../../api/connections'
import { errorMessage } from '../../api/messages'
import { useGuard } from '../../auth/prompts'
import { Field, Notice, Section, useAction } from '../forms'
import { FORGET_WARNING } from './mowlWarning'

// Plain words for the ADR 0004 section 5 categories. The raw code is never shown.
const CONNECT_ERRORS: Record<string, string> = {
  invalid_credentials: 'MOWL did not accept that email and password.',
  upstream_unavailable: 'MOWL could not be reached. Try again in a few minutes.',
  unknown: 'MOWL answered in a way classbeat did not expect. Try again later.',
}

const LAST_ERRORS: Record<string, string> = {
  needs_reconnect: 'MOWL no longer accepts the stored sign-in. Connect again.',
  decrypt_failed: 'The stored sign-in could not be read. Connect again.',
  rate_limited: 'MOWL asked classbeat to slow down.',
  upstream_unavailable: 'MOWL could not be reached the last time.',
}

function connectError(error: unknown): string {
  if (error instanceof ApiError && error.status !== 429 && error.reason !== null) {
    const known = Object.hasOwn(CONNECT_ERRORS, error.reason) ? CONNECT_ERRORS[error.reason] : undefined
    if (known !== undefined) return known
  }
  return errorMessage(error)
}

function lastErrorText(category: string): string {
  const known = Object.hasOwn(LAST_ERRORS, category) ? LAST_ERRORS[category] : undefined
  return known ?? 'The last use of this connection failed.'
}

function since(iso: string): string {
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleDateString()
}

/** MOWL (Intelligent Cycling): connect with email and password, see the status, disconnect. */
export function Connections() {
  const guard = useGuard()
  const [mowl, setMowl] = useState<Connection | null | undefined>(undefined)
  const [loadFailed, setLoadFailed] = useState(false)
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [note, setNote] = useState<string | null>(null)
  const [confirmingDisconnect, setConfirmingDisconnect] = useState(false)
  const action = useAction()

  const load = useCallback(async () => {
    const all = await getConnections()
    setMowl(all.find((item) => item.provider === 'mowl') ?? null)
  }, [])

  useEffect(() => {
    let current = true
    getConnections().then(
      (all) => {
        if (current) setMowl(all.find((item) => item.provider === 'mowl') ?? null)
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
    const sent = { email: email.trim(), password }
    void action
      .run(async () => {
        setMowl(await guard(() => connectMowl(sent.email, sent.password)))
        setNote('MOWL is connected.')
      }, connectError)
      .finally(() => {
        setPassword('') // the password is not kept on the page after the attempt
      })
    setEmail(sent.email)
  }

  const disconnect = () => {
    setNote(null)
    setConfirmingDisconnect(false)
    void action.run(async () => {
      await guard(() => disconnectMowl())
      await load()
      setNote('MOWL is disconnected. classbeat no longer uses its sign-in.')
    })
  }

  const needsForm = mowl === null || mowl?.status === 'needs_reconnect'
  return (
    <Section title="MOWL (Intelligent Cycling)">
      {note ? <Notice kind="success">{note}</Notice> : null}
      {action.error ? <Notice kind="error">{action.error}</Notice> : null}
      {loadFailed ? <Notice kind="error">The connections could not be loaded.</Notice> : null}
      {mowl === undefined && !loadFailed ? <p>Loading</p> : null}
      {mowl ? (
        <ul className="rows">
          <li>
            <span>Status</span>
            <span data-testid="mowl-status">
              {mowl.status === 'connected' ? 'Connected' : 'Needs to be connected again'}
            </span>
          </li>
          <li>
            <span>Connected since</span>
            <span className="muted">{since(mowl.connectedAt)}</span>
          </li>
          {mowl.lastError ? (
            <li>
              <span>Last problem</span>
              <span className="muted">{lastErrorText(mowl.lastError)}</span>
            </li>
          ) : null}
        </ul>
      ) : null}
      {needsForm ? (
        <form onSubmit={connect}>
          {mowl ? (
            <p className="notice" data-kind="info" data-testid="reconnect-warning">
              Connecting again replaces the stored sign-in. {FORGET_WARNING}
            </p>
          ) : null}
          <p>
            Sign in with your Intelligent Cycling (MOWL) account. classbeat uses your password once
            to get a sign-in token and does not keep it.
          </p>
          <Field
            label="MOWL email"
            type="email"
            autoComplete="username"
            value={email}
            onChange={(e) => {
              setEmail(e.target.value)
            }}
            required
          />
          <Field
            label="MOWL password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => {
              setPassword(e.target.value)
            }}
            required
          />
          <button className="button" type="submit" disabled={action.busy}>
            {mowl ? 'Connect MOWL again' : 'Connect MOWL'}
          </button>
        </form>
      ) : null}
      {mowl ? (
        <>
          <p className="muted">
            Disconnecting deletes the token classbeat holds. MOWL has no way for classbeat to end
            the sign-in on its side; change your MOWL password to end it there.
          </p>
          {confirmingDisconnect ? (
            <div className="notice" role="group" aria-label="Confirm disconnect">
              <p>{FORGET_WARNING}</p>
              <button className="button" type="button" disabled={action.busy} onClick={disconnect}>
                Yes, disconnect MOWL
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
              Disconnect MOWL
            </button>
          )}
        </>
      ) : null}
    </Section>
  )
}
