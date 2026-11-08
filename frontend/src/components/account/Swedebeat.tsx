import { useCallback, useEffect, useId, useState, type FormEvent } from 'react'
import { ApiError } from '../../api/client'
import {
  connectSwedebeat,
  disconnectSwedebeat,
  getConnectionsView,
  setSwedebeatPreference,
  type Connection,
  type SwedebeatAccess,
} from '../../api/connections'
import { errorMessage } from '../../api/messages'
import { useGuard } from '../../auth/prompts'
import type { CatalogueKey } from '../../i18n/en'
import { date as formatDate } from '../../i18n/format'
import { useT } from '../../i18n/I18n'
import { t as translate } from '../../i18n/t'
import { Field, Notice, Section, useAction } from '../forms'
import { Switch } from '../ui/switch'

// Plain words for the connect failures (ADR 0015 section 7). The raw code is never shown.
const CONNECT_ERRORS: Record<string, CatalogueKey> = {
  invalid_credentials: 'swed.err.invalid_credentials',
  upstream_unavailable: 'swed.err.upstream_unavailable',
  app_key_unavailable: 'swed.err.app_key_unavailable',
  feature_off: 'swed.err.feature_off',
  unknown: 'swed.err.unknown',
}

const LAST_ERRORS: Record<string, CatalogueKey> = {
  needs_reconnect: 'swed.last.needs_reconnect',
  decrypt_failed: 'swed.last.decrypt_failed',
  rate_limited: 'swed.last.rate_limited',
  upstream_unavailable: 'swed.last.upstream_unavailable',
}

function lookup(table: Record<string, CatalogueKey>, key: string): CatalogueKey | undefined {
  return Object.hasOwn(table, key) ? table[key] : undefined
}

function connectError(error: unknown): string {
  if (error instanceof ApiError && error.status !== 429 && error.reason !== null) {
    const known = lookup(CONNECT_ERRORS, error.reason)
    if (known !== undefined) return translate(known)
  }
  return errorMessage(error)
}

function since(iso: string): string {
  return formatDate(iso)
}

/**
 * Swedebeat (ADR 0015, ADR 0013): optional, for instructors who teach with Swedebeat music.
 * The card exists only while the effective flag is on, or while the user still holds a Swedebeat
 * connection (so Disconnect is always reachable). With the flag on it starts with the user's own
 * "I use Swedebeat" switch (#525, default off); everything else Swedebeat needs that switch on.
 * On, and the server holds Swedebeat's app key, it shows
 * the connect form (email and password, used once, never kept), then the connected state with the
 * email, and a confirmed disconnect. If another device holds the Swedebeat session, the user is
 * asked before it is signed out.
 */
export function Swedebeat() {
  const t = useT()
  const guard = useGuard()
  const [view, setView] = useState<{ access: SwedebeatAccess; account: Connection | null } | null>(null)
  const [loadFailed, setLoadFailed] = useState(false)
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [note, setNote] = useState<string | null>(null)
  const [taken, setTaken] = useState(false)
  const [confirmingDisconnect, setConfirmingDisconnect] = useState(false)
  const action = useAction()
  const switchId = useId()

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
        setNote(t('swed.connected'))
      }, (error) => {
        if (error instanceof ApiError && error.reason === 'device_in_use') {
          setTaken(true)
          return t('swed.deviceInUse')
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
      setNote(t('swed.disconnected'))
    })
  }

  const choose = (on: boolean) => {
    setNote(null)
    setTaken(false)
    void action.run(async () => {
      const stored = await setSwedebeatPreference(on)
      setView((was) => (was === null ? was : { ...was, access: { ...was.access, usesSwedebeat: stored } }))
    })
  }

  // Nothing about Swedebeat shows until it is known whether this user may see it.
  if (view === null) {
    return loadFailed ? (
      <Section title={t('swed.title')}>
        <Notice kind="error">{t('swed.loadFailed')}</Notice>
      </Section>
    ) : null
  }

  const { access, account } = view
  // A note ("Swedebeat is disconnected.") keeps the card until the page is loaded again.
  if (!access.enabled && account === null && note === null) return null
  const on = access.enabled && access.usesSwedebeat
  const needsForm = on && access.available && (account === null || account.status === 'needs_reconnect')
  return (
    <Section title={t('swed.title')}>
      {note ? <Notice kind="success">{note}</Notice> : null}
      {action.error ? <Notice kind="error">{action.error}</Notice> : null}
      {access.enabled ? (
        <div className="flex flex-col gap-2">
          <div className="flex items-center gap-3">
            <Switch
              id={switchId}
              checked={access.usesSwedebeat}
              disabled={action.busy}
              onCheckedChange={choose}
            />
            <label htmlFor={switchId} className="font-medium">
              {t('swed.use')}
            </label>
          </div>
          <p className="m-0 text-muted" data-testid="swedebeat-preference-state">
            {access.usesSwedebeat ? t('swed.useOn') : t('swed.useOff')}
          </p>
        </div>
      ) : null}
      {on && !access.available && account === null ? (
        <p className="text-muted">{t('swed.notAvailable')}</p>
      ) : null}
      {account ? (
        <ul className="rows">
          <li>
            <span>{t('mowl.status')}</span>
            <span data-testid="swedebeat-status">
              {account.status === 'connected' ? t('mowl.statusConnected') : t('mowl.statusAgain')}
            </span>
          </li>
          {account.account ? (
            <li>
              <span>{t('swed.account')}</span>
              <span data-testid="swedebeat-account">{account.account}</span>
            </li>
          ) : null}
          <li>
            <span>{t('mowl.since')}</span>
            <span className="muted">{since(account.connectedAt)}</span>
          </li>
          {account.lastError ? (
            <li>
              <span>{t('mowl.lastProblem')}</span>
              <span className="muted">{t(lookup(LAST_ERRORS, account.lastError) ?? 'swed.last.other')}</span>
            </li>
          ) : null}
        </ul>
      ) : null}
      {needsForm ? (
        <form onSubmit={connect}>
          <p>{t('swed.intro')}</p>
          <Field
            label={t('swed.email')}
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
            label={t('swed.password')}
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
              {t('swed.again')}
            </p>
          ) : null}
          <button className="button" type="submit" disabled={action.busy}>
            {taken ? t('swed.anyway') : account ? t('swed.connectAgain') : t('swed.connect')}
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
              {t('swed.keepOther')}
            </button>
          ) : null}
        </form>
      ) : null}
      {account ? (
        <>
          <p className="muted">{t('swed.disconnectNote')}</p>
          {confirmingDisconnect ? (
            <div className="notice" role="group" aria-label={t('swed.confirmLabel')}>
              <p>{t('swed.confirmText')}</p>
              <button className="button" type="button" disabled={action.busy} onClick={disconnect}>
                {t('swed.disconnectYes')}
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
              {t('swed.disconnect')}
            </button>
          )}
        </>
      ) : null}
    </Section>
  )
}
