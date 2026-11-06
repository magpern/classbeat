import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { ApiError } from '../../api/client'
import { errorMessage } from '../../api/messages'
import {
  disconnectSpotify,
  getSpotifyStatus,
  startSpotify,
  type SpotifyStatus,
} from '../../api/spotify'
import { sentenceText } from '../../api/sentences'
import { parseRetryAfter, spotifySentence } from '../../api/spotifyMessages'
import { useGuard } from '../../auth/prompts'
import type { CatalogueKey } from '../../i18n/en'
import { useT } from '../../i18n/I18n'
import { t as translate } from '../../i18n/t'
import { goToProvider } from '../../lib/navigation'
import { Link, useQuery } from '../../router/Router'
import { Field, Notice, Section, useAction } from '../forms'
import { ConsentSwitch } from './ConsentSwitch'

// ADR 0009 section 2 (the wizard), 7 (what Disconnect and a changed Client ID delete), 8 (the
// sentences) and 9 (the consent). Nothing here prints a token, a Spotify id or a secret, and no
// field asks for a Client Secret: Spotify sign-in is PKCE.

const CLIENT_ID = /^[0-9a-fA-F]{32}$/
const STATUS_TEXT: Record<string, CatalogueKey> = {
  connected: 'mowl.statusConnected',
  needs_reconnect: 'mowl.statusAgain',
  not_connected: 'spot.notConnected',
}

function statusKey(status: string): CatalogueKey {
  return (Object.hasOwn(STATUS_TEXT, status) ? STATUS_TEXT[status] : undefined) ?? 'spot.notConnected'
}

function startError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 422) return translate('spot.invalidClientId')
    if (error.status === 404 && error.reason === 'feature_off') return translate('spot.featureOff')
  }
  return errorMessage(error)
}

type Dialog = 'disconnect' | 'change' | null

function useSpotifyStatus() {
  const [status, setStatus] = useState<SpotifyStatus | null | undefined>(undefined)
  const [loadFailed, setLoadFailed] = useState(false)

  const reload = useCallback(async () => {
    setStatus(await getSpotifyStatus())
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    getSpotifyStatus(controller.signal).then(
      (found) => {
        if (!controller.signal.aborted) setStatus(found)
      },
      () => {
        if (!controller.signal.aborted) setLoadFailed(true)
      },
    )
    return () => {
      controller.abort()
    }
  }, [])
  return { status, loadFailed, reload }
}

/**
 * The account page's Spotify card: the status, the working-playlist consent switch once connected,
 * and a link to the wizard. Hidden while song picking is off.
 */
export function SpotifySummary() {
  const t = useT()
  const { status, reload } = useSpotifyStatus()
  if (!status) return null
  return (
    <Section title={t('spot.title')}>
      <ul className="rows">
        <li>
          <span>{t('mowl.status')}</span>
          <span data-testid="spotify-summary-status">
            {t(statusKey(status.status))}
          </span>
        </li>
      </ul>
      {status.status === 'connected' ? <ConsentSwitch status={status} onChanged={reload} /> : null}
      <p>
        <Link to="/account/spotify">
          {status.status === 'not_connected' ? t('spot.setUp') : t('spot.manage')}
        </Link>
      </p>
    </Section>
  )
}

/** The Spotify wizard, the connection card and the consent step (the page /account/spotify). */
export function SpotifyConnection() {
  const t = useT()
  const guard = useGuard()
  const query = useQuery()
  const result = query.get('result')
  const retryAfter = parseRetryAfter(query.get('retry_after'))
  const { status, loadFailed, reload } = useSpotifyStatus()
  const [clientId, setClientId] = useState('')
  const [invalid, setInvalid] = useState(false)
  const [note, setNote] = useState<string | null>(null)
  const [dialog, setDialog] = useState<Dialog>(null)
  const [changing, setChanging] = useState(false)
  const [copy, setCopy] = useState<'copied' | 'failed' | null>(null)
  const action = useAction()
  // After `account_changed` the next start carries the user's confirmation.
  const confirmSwitch = result === 'account_changed'

  const run = (task: () => Promise<void>, done: string) => {
    setNote(null)
    setDialog(null)
    void action.run(async () => {
      await task()
      await reload()
      setNote(done)
    })
  }

  const connect = (event: FormEvent) => {
    event.preventDefault()
    setNote(null)
    const sent = clientId.trim()
    if (!CLIENT_ID.test(sent)) {
      // Blocked here, nothing is sent, and the value is not repeated back.
      setInvalid(true)
      return
    }
    setInvalid(false)
    void action.run(async () => {
      goToProvider(await guard(() => startSpotify(sent, confirmSwitch)))
    }, startError)
  }

  const copyUrl = (url: string) => {
    const done = (outcome: 'copied' | 'failed') => {
      setCopy(outcome)
    }
    try {
      navigator.clipboard.writeText(url).then(
        () => {
          done('copied')
        },
        () => {
          done('failed')
        },
      )
    } catch {
      done('failed') // no clipboard in this browser context
    }
  }

  if (loadFailed) return <Notice kind="error">{t('spot.loadFailed')}</Notice>
  if (status === undefined) return <p>{t('spot.loading')}</p>
  if (status === null) return <Notice>{t('spot.featureOff')}</Notice>

  const connected = status.status === 'connected'
  const showWizard = !connected || changing || confirmSwitch
  const lastError =
    status.lastError === null
      ? null
      : (sentenceText(t, status.lastErrorSentence, status.lastErrorMessage) ??
        spotifySentence(status.lastError))

  return (
    <div className="stack">
      <h1>{t('spot.title')}</h1>
      {result === 'connected' ? (
        <Notice kind="success">{t('spot.connected')}</Notice>
      ) : result ? (
        <Notice kind="error">{spotifySentence(result, retryAfter)}</Notice>
      ) : null}
      {note ? <Notice kind="success">{note}</Notice> : null}
      {action.error ? <Notice kind="error">{action.error}</Notice> : null}

      <Section title={t('spot.connectionTitle')}>
        <ul className="rows">
          <li>
            <span>{t('mowl.status')}</span>
            <span data-testid="spotify-status">
              {t(statusKey(status.status))}
            </span>
          </li>
          {lastError !== null ? (
            <li>
              <span>{t('mowl.lastProblem')}</span>
              <span className="muted" data-testid="spotify-last-error">
                {lastError}
              </span>
            </li>
          ) : null}
        </ul>

        {showWizard ? (
          <form onSubmit={connect} noValidate>
            <p>{t('spot.intro')}</p>
            <ol>
              <li>{t('spot.step1')}</li>
              <li>
                {t('spot.step2')}
                <div className="field">
                  <code data-testid="spotify-callback-url" className="break-all">{status.callbackUrl}</code>{' '}
                  <button
                    className="button button--secondary"
                    type="button"
                    onClick={() => {
                      copyUrl(status.callbackUrl)
                    }}
                  >
                    {t('spot.copyUrl')}
                  </button>
                  {copy === 'copied' ? <span role="status">{t('spot.copied')}</span> : null}
                  {copy === 'failed' ? (
                    <span role="status">{t('spot.copyFailed')}</span>
                  ) : null}
                </div>
              </li>
              <li>{t('spot.step3')}</li>
              <li>{t('spot.step4')}</li>
              <li>{t('spot.step5')}</li>
            </ol>
            {changing || confirmSwitch ? (
              <p className="muted">
                {confirmSwitch
                  ? t('spot.confirmHint')
                  : t('spot.changeText')}
              </p>
            ) : null}
            <Field
              label={t('spot.clientId')}
              type="text"
              autoComplete="off"
              autoCapitalize="off"
              spellCheck={false}
              value={clientId}
              aria-invalid={invalid}
              onChange={(e) => {
                setClientId(e.target.value)
                setInvalid(false)
              }}
            />
            {invalid ? <Notice kind="error">{t('spot.invalidClientId')}</Notice> : null}
            <button className="button" type="submit" disabled={action.busy}>
              {confirmSwitch
                ? t('spot.confirmSwitch')
                : status.status === 'needs_reconnect'
                  ? t('spot.connectAgain')
                  : t('spot.connect')}
            </button>
            {changing ? (
              <button
                className="button button--secondary"
                type="button"
                onClick={() => {
                  setChanging(false)
                  setInvalid(false)
                }}
              >
                {t('spot.keepId')}
              </button>
            ) : null}
          </form>
        ) : null}

        {status.status !== 'not_connected' ? (
          <>
            {connected && !changing ? (
              <>
                <button
                  className="button button--secondary"
                  type="button"
                  disabled={action.busy}
                  onClick={() => {
                    setDialog('change')
                  }}
                >
                  {t('spot.changeId')}
                </button>
                {dialog === 'change' ? (
                  <div className="notice" role="group" aria-label={t('spot.changeConfirm')}>
                    <p>{t('spot.changeText')}</p>
                    <button
                      className="button"
                      type="button"
                      onClick={() => {
                        setDialog(null)
                        setChanging(true)
                      }}
                    >
                      {t('spot.continue')}
                    </button>
                    <button
                      className="button button--secondary"
                      type="button"
                      onClick={() => {
                        setDialog(null)
                      }}
                    >
                      {t('spot.keepId')}
                    </button>
                  </div>
                ) : null}
              </>
            ) : null}
            {dialog === 'disconnect' ? (
              <div className="notice" role="group" aria-label={t('spot.disconnectConfirm')}>
                <p>{t('spot.disconnectText')}</p>
                <button
                  className="button"
                  type="button"
                  disabled={action.busy}
                  onClick={() => {
                    run(
                      () => guard(() => disconnectSpotify()),
                      t('spot.disconnected'),
                    )
                  }}
                >
                  {t('spot.disconnectYes')}
                </button>
                <button
                  className="button button--secondary"
                  type="button"
                  onClick={() => {
                    setDialog(null)
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
                  setDialog('disconnect')
                }}
              >
                {t('spot.disconnect')}
              </button>
            )}
          </>
        ) : null}
      </Section>

      {connected ? (
        <Section title={t('spot.workingTitle')}>
          <ConsentSwitch status={status} onChanged={reload} />
        </Section>
      ) : null}
    </div>
  )
}
