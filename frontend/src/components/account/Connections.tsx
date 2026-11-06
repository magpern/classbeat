import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { ApiError } from '../../api/client'
import {
  connectMowl,
  disconnectMowl,
  getConnectionsView,
  type Connection,
} from '../../api/connections'
import { errorMessage } from '../../api/messages'
import { useGuard } from '../../auth/prompts'
import { date as formatDate } from '../../i18n/format'
import type { CatalogueKey } from '../../i18n/en'
import { useT } from '../../i18n/I18n'
import { t as translate } from '../../i18n/t'
import { useLayout } from '../../hooks/useLayout'
import { Field, Notice, Section, useAction } from '../forms'
import { MowlQr } from './MowlQr'
import { FORGET_WARNING } from './mowlWarning'

// Plain words for the ADR 0004 section 5 categories. The raw code is never shown.
const CONNECT_ERRORS: Record<string, CatalogueKey> = {
  invalid_credentials: 'mowl.err.invalid_credentials',
  upstream_unavailable: 'mowl.err.upstream_unavailable',
  unknown: 'mowl.err.unknown',
}

const LAST_ERRORS: Record<string, CatalogueKey> = {
  needs_reconnect: 'mowl.last.needs_reconnect',
  decrypt_failed: 'mowl.last.decrypt_failed',
  rate_limited: 'mowl.last.rate_limited',
  upstream_unavailable: 'mowl.last.upstream_unavailable',
}

function connectError(error: unknown): string {
  if (error instanceof ApiError && error.status !== 429 && error.reason !== null) {
    const known = Object.hasOwn(CONNECT_ERRORS, error.reason) ? CONNECT_ERRORS[error.reason] : undefined
    if (known !== undefined) return translate(known)
  }
  return errorMessage(error)
}

function lastErrorText(category: string): string {
  const known = Object.hasOwn(LAST_ERRORS, category) ? LAST_ERRORS[category] : undefined
  return translate(known ?? 'mowl.last.other')
}

function since(iso: string): string {
  return formatDate(iso)
}

/** MOWL (Intelligent Cycling): connect with email and password, see the status, disconnect. */
export function Connections() {
  const t = useT()
  const guard = useGuard()
  const [mowl, setMowl] = useState<Connection | null | undefined>(undefined)
  // ADR 0024: the QR panel, when the server offers it and the layout is wider than a phone's.
  const [qrEnabled, setQrEnabled] = useState(false)
  const layout = useLayout()
  const [loadFailed, setLoadFailed] = useState(false)
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [note, setNote] = useState<string | null>(null)
  const [confirmingDisconnect, setConfirmingDisconnect] = useState(false)
  const action = useAction()

  const load = useCallback(async () => {
    const view = await getConnectionsView()
    setMowl(view.connections.find((item) => item.provider === 'mowl') ?? null)
  }, [])

  useEffect(() => {
    let current = true
    getConnectionsView().then(
      (view) => {
        if (!current) return
        setMowl(view.connections.find((item) => item.provider === 'mowl') ?? null)
        setQrEnabled(view.mowlQrEnabled)
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
        setNote(t('mowl.connected'))
      }, connectError)
      .finally(() => {
        setPassword('') // the password is not kept on the page after the attempt
      })
    setEmail(sent.email)
  }

  const scanned = useCallback(
    (connection: Connection) => {
      setMowl(connection)
      setNote(t('mowl.connected'))
    },
    [t],
  )

  const disconnect = () => {
    setNote(null)
    setConfirmingDisconnect(false)
    void action.run(async () => {
      await guard(() => disconnectMowl())
      await load()
      setNote(t('mowl.disconnected'))
    })
  }

  const needsForm = mowl === null || mowl?.status === 'needs_reconnect'
  return (
    <Section title={t('mowl.title')}>
      {note ? <Notice kind="success">{note}</Notice> : null}
      {action.error ? <Notice kind="error">{action.error}</Notice> : null}
      {loadFailed ? <Notice kind="error">{t('mowl.loadFailed')}</Notice> : null}
      {mowl === undefined && !loadFailed ? <p>{t('common.loading')}</p> : null}
      {mowl ? (
        <ul className="rows">
          <li>
            <span>{t('mowl.status')}</span>
            <span data-testid="mowl-status">
              {mowl.status === 'connected' ? t('mowl.statusConnected') : t('mowl.statusAgain')}
            </span>
          </li>
          <li>
            <span>{t('mowl.since')}</span>
            <span className="muted">{since(mowl.connectedAt)}</span>
          </li>
          {mowl.lastError ? (
            <li>
              <span>{t('mowl.lastProblem')}</span>
              <span className="muted">{lastErrorText(mowl.lastError)}</span>
            </li>
          ) : null}
        </ul>
      ) : null}
      {needsForm && mowl ? (
        <p className="notice" data-kind="info" data-testid="reconnect-warning">
          {t('mowl.reconnectWarning', { warning: FORGET_WARNING })}
        </p>
      ) : null}
      {needsForm && qrEnabled && layout !== 'phone' ? <MowlQr onConnected={scanned} /> : null}
      {needsForm ? (
        <form onSubmit={connect}>
          <p>{t('mowl.signInText')}</p>
          <Field
            label={t('mowl.emailLabel')}
            type="email"
            autoComplete="username"
            value={email}
            onChange={(e) => {
              setEmail(e.target.value)
            }}
            required
          />
          <Field
            label={t('mowl.passwordLabel')}
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => {
              setPassword(e.target.value)
            }}
            required
          />
          <button className="button" type="submit" disabled={action.busy}>
            {mowl ? t('mowl.connectAgainButton') : t('mowl.connectButton')}
          </button>
        </form>
      ) : null}
      {mowl ? (
        <>
          <p className="muted">{t('mowl.disconnectText')}</p>
          {confirmingDisconnect ? (
            <div className="notice" role="group" aria-label={t('mowl.confirmDisconnect')}>
              <p>{FORGET_WARNING}</p>
              <button className="button" type="button" disabled={action.busy} onClick={disconnect}>
                {t('mowl.yesDisconnect')}
              </button>
              <button
                className="button button--secondary"
                type="button"
                onClick={() => {
                  setConfirmingDisconnect(false)
                }}
              >
                {t('mowl.keep')}
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
              {t('mowl.disconnect')}
            </button>
          )}
        </>
      ) : null}
    </Section>
  )
}
