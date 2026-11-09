import { useCallback, useEffect, useId, useState } from 'react'
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
import { useQuery } from '../../router/Router'
import { Notice, useAction } from '../forms'
import { ConsentSwitch } from './ConsentSwitch'
import { SpotifyWizard } from './SpotifyWizard'

// ADR 0009 section 2 (the wizard), 7 (what Disconnect and a changed Client ID delete), 8 (the
// sentences) and 9 (the consent). Nothing here prints a token, a Spotify id or a secret, and no
// field asks for a Client Secret: Spotify sign-in is PKCE.

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
 * The body of the Spotify dialog on Account, Connections: the callback result (`?result=` on
 * /account/spotify), the status, the wizard, Change Client ID, Disconnect and the consent step.
 * `onStatus` hears every status it loads, so the Connections row stays in step.
 */
export function SpotifyConnection({ onStatus }: { onStatus?: (status: SpotifyStatus | null) => void }) {
  const t = useT()
  const guard = useGuard()
  const query = useQuery()
  const result = query.get('result')
  const retryAfter = parseRetryAfter(query.get('retry_after'))
  const { status, loadFailed, reload } = useSpotifyStatus()
  const [note, setNote] = useState<string | null>(null)
  const [dialog, setDialog] = useState<Dialog>(null)
  const [changing, setChanging] = useState(false)
  const action = useAction()
  const workingId = useId()
  // After `account_changed` the next start carries the user's confirmation.
  const confirmSwitch = result === 'account_changed'

  useEffect(() => {
    if (status !== undefined) onStatus?.(status)
  }, [status, onStatus])

  const run = (task: () => Promise<void>, done: string) => {
    setNote(null)
    setDialog(null)
    void action.run(async () => {
      await task()
      await reload()
      setNote(done)
    })
  }

  const start = (clientId: string) => {
    setNote(null)
    void action.run(async () => {
      goToProvider(await guard(() => startSpotify(clientId, confirmSwitch)))
    }, startError)
  }

  if (loadFailed) return <Notice kind="error">{t('spot.loadFailed')}</Notice>
  if (status === undefined) return <p>{t('spot.loading')}</p>
  if (status === null) return <Notice>{t('spot.featureOff')}</Notice>

  const connected = status.status === 'connected'
  const lastError =
    status.lastError === null
      ? null
      : (sentenceText(t, status.lastErrorSentence, status.lastErrorMessage) ??
        spotifySentence(status.lastError))
  const secondary = 'button button--secondary'

  return (
    <div className="flex flex-col gap-4">
      {result === 'connected' ? (
        <Notice kind="success">{t('spot.connected')}</Notice>
      ) : result ? (
        <Notice kind="error">{spotifySentence(result, retryAfter)}</Notice>
      ) : null}
      {note ? <Notice kind="success">{note}</Notice> : null}
      {action.error ? <Notice kind="error">{action.error}</Notice> : null}
      <ul className="rows">
        <li>
          <span>{t('mowl.status')}</span>
          <span data-testid="spotify-status">{t(statusKey(status.status))}</span>
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
      {!connected || changing || confirmSwitch ? (
        <SpotifyWizard
          status={status}
          confirmSwitch={confirmSwitch}
          changing={changing}
          busy={action.busy}
          onStart={start}
          onKeep={() => {
            setChanging(false)
          }}
        />
      ) : null}
      {connected && !changing && dialog === 'change' ? (
        <div className="notice" role="group" aria-label={t('spot.changeConfirm')}>
          <p>{t('spot.changeText')}</p>
          <div className="flex flex-wrap gap-2">
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
              className={secondary}
              type="button"
              onClick={() => {
                setDialog(null)
              }}
            >
              {t('spot.keepId')}
            </button>
          </div>
        </div>
      ) : null}
      {dialog === 'disconnect' ? (
        <div className="notice" role="group" aria-label={t('spot.disconnectConfirm')}>
          <p>{t('spot.disconnectText')}</p>
          <div className="flex flex-wrap gap-2">
            <button
              className="button"
              type="button"
              disabled={action.busy}
              onClick={() => {
                run(() => guard(() => disconnectSpotify()), t('spot.disconnected'))
              }}
            >
              {t('spot.disconnectYes')}
            </button>
            <button
              className={secondary}
              type="button"
              onClick={() => {
                setDialog(null)
              }}
            >
              {t('mowl.keep')}
            </button>
          </div>
        </div>
      ) : null}
      {status.status !== 'not_connected' && dialog === null ? (
        <div className="flex flex-wrap gap-2">
          {connected && !changing ? (
            <button
              className={secondary}
              type="button"
              disabled={action.busy}
              onClick={() => {
                setDialog('change')
              }}
            >
              {t('spot.changeId')}
            </button>
          ) : null}
          <button
            className={secondary}
            type="button"
            disabled={action.busy}
            onClick={() => {
              setDialog('disconnect')
            }}
          >
            {t('spot.disconnect')}
          </button>
        </div>
      ) : null}
      {connected ? (
        <section aria-labelledby={workingId} className="flex flex-col gap-3">
          <h3 id={workingId} className="m-0 font-display text-lg font-bold">
            {t('spot.workingTitle')}
          </h3>
          <ConsentSwitch status={status} onChanged={reload} />
        </section>
      ) : null}
    </div>
  )
}
