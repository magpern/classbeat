import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { ApiError } from '../../api/client'
import { errorMessage } from '../../api/messages'
import {
  disconnectSpotify,
  getSpotifyStatus,
  giveSpotifyConsent,
  revokeSpotifyConsent,
  startSpotify,
  type SpotifyStatus,
} from '../../api/spotify'
import { parseRetryAfter, spotifySentence } from '../../api/spotifyMessages'
import { useGuard } from '../../auth/prompts'
import { goToProvider } from '../../lib/navigation'
import { Link, useQuery } from '../../router/Router'
import { Field, Notice, Section, useAction } from '../forms'

// ADR 0009 section 2 (the wizard), 7 (what Disconnect and a changed Client ID delete), 8 (the
// sentences) and 9 (the consent). Nothing here prints a token, a Spotify id or a secret, and no
// field asks for a Client Secret: Spotify sign-in is PKCE.

const CLIENT_ID = /^[0-9a-fA-F]{32}$/
const INVALID_CLIENT_ID =
  'That does not look like a Spotify Client ID. It is 32 letters and digits (0-9 and a-f), shown on your app’s settings page in the Spotify Developer Dashboard.'
const FEATURE_OFF = 'Song picking is not available right now.'

export const DISCONNECT_TEXT =
  'Disconnecting deletes what classbeat holds for Spotify: the stored sign-in, the working playlist setting and your consent for it, any playlist step that was left undecided, and the link between each class and the playlist classbeat made for it (the classes themselves stay). Playlists already in your Spotify stay there and classbeat will not touch them again. Spotify gives classbeat no way to end the connection on its side: remove classbeat’s access under Apps in your Spotify account settings.'
export const CHANGE_CLIENT_ID_TEXT =
  'classbeat keeps using the current connection until the new Client ID has worked. Then it replaces the stored sign-in and forgets the working playlist setting, so you are asked for your consent again; the playlist stays in your Spotify. Your classes keep their playlists. If the new app signs in a different Spotify account, you must confirm the switch, and classbeat then deletes what Disconnect deletes.'
export const REVOKE_TEXT =
  'Revoking makes classbeat forget the working playlist and stop using it. The playlist stays in your Spotify, private, with its last songs; delete it there if you do not want it. Picking songs needs your consent again, and the next pick creates a new working playlist.'

const STATUS_TEXT: Record<string, string> = {
  connected: 'Connected',
  needs_reconnect: 'Needs to be connected again',
  not_connected: 'Not connected',
}

function startError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 422) return INVALID_CLIENT_ID
    if (error.status === 404 && error.reason === 'feature_off') return FEATURE_OFF
  }
  return errorMessage(error)
}

type Dialog = 'disconnect' | 'change' | 'revoke' | null

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

/** The account page's short card: the status and a link. Hidden while song picking is off. */
export function SpotifySummary() {
  const { status } = useSpotifyStatus()
  if (!status) return null
  return (
    <Section title="Spotify">
      <ul className="rows">
        <li>
          <span>Status</span>
          <span data-testid="spotify-summary-status">
            {Object.hasOwn(STATUS_TEXT, status.status)
              ? STATUS_TEXT[status.status]
              : STATUS_TEXT.not_connected}
          </span>
        </li>
      </ul>
      <p>
        <Link to="/account/spotify">
          {status.status === 'not_connected' ? 'Set up Spotify' : 'Manage Spotify'}
        </Link>
      </p>
    </Section>
  )
}

/** The Spotify wizard, the connection card and the consent step (the page /account/spotify). */
export function SpotifyConnection() {
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

  if (loadFailed) return <Notice kind="error">The Spotify connection could not be loaded.</Notice>
  if (status === undefined) return <p>Loading</p>
  if (status === null) return <Notice>{FEATURE_OFF}</Notice>

  const connected = status.status === 'connected'
  const showWizard = !connected || changing || confirmSwitch
  const lastError =
    status.lastError === null
      ? null
      : (status.lastErrorMessage ?? spotifySentence(status.lastError))

  return (
    <div className="stack">
      <h1>Spotify</h1>
      {result === 'connected' ? (
        <Notice kind="success">Spotify is connected.</Notice>
      ) : result ? (
        <Notice kind="error">{spotifySentence(result, retryAfter)}</Notice>
      ) : null}
      {note ? <Notice kind="success">{note}</Notice> : null}
      {action.error ? <Notice kind="error">{action.error}</Notice> : null}

      <Section title="Spotify connection">
        <ul className="rows">
          <li>
            <span>Status</span>
            <span data-testid="spotify-status">
              {Object.hasOwn(STATUS_TEXT, status.status)
                ? STATUS_TEXT[status.status]
                : STATUS_TEXT.not_connected}
            </span>
          </li>
          {lastError !== null ? (
            <li>
              <span>Last problem</span>
              <span className="muted" data-testid="spotify-last-error">
                {lastError}
              </span>
            </li>
          ) : null}
        </ul>

        {showWizard ? (
          <form onSubmit={connect} noValidate>
            <p>
              classbeat connects to a Spotify app of your own, so the limits are yours. Follow
              these steps once.
            </p>
            <ol>
              <li>
                Open the Spotify Developer Dashboard (developer.spotify.com/dashboard) and create
                an app. The person who owns the app needs Spotify Premium. A Development Mode app
                has an allow-list of 5 people: under User Management add your own Spotify account
                (name and email). Spotify&rsquo;s endpoint and rate limits apply.
              </li>
              <li>
                Copy classbeat&rsquo;s callback URL:
                <div className="field">
                  <code data-testid="spotify-callback-url">{status.callbackUrl}</code>{' '}
                  <button
                    className="button button--secondary"
                    type="button"
                    onClick={() => {
                      copyUrl(status.callbackUrl)
                    }}
                  >
                    Copy callback URL
                  </button>
                  {copy === 'copied' ? <span role="status"> Copied.</span> : null}
                  {copy === 'failed' ? (
                    <span role="status"> Could not copy. Select the address and copy it.</span>
                  ) : null}
                </div>
              </li>
              <li>Paste it into your app&rsquo;s Redirect URIs and save.</li>
              <li>
                Copy your app&rsquo;s Client ID into the field below. classbeat never needs the
                app&rsquo;s Client Secret and does not ask for it.
              </li>
              <li>Press the button to sign in at Spotify and allow classbeat.</li>
            </ol>
            {changing || confirmSwitch ? (
              <p className="muted">
                {confirmSwitch
                  ? 'Enter the Client ID again and confirm to switch to the other Spotify account.'
                  : CHANGE_CLIENT_ID_TEXT}
              </p>
            ) : null}
            <Field
              label="Spotify Client ID"
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
            {invalid ? <Notice kind="error">{INVALID_CLIENT_ID}</Notice> : null}
            <button className="button" type="submit" disabled={action.busy}>
              {confirmSwitch
                ? 'Confirm and switch account'
                : status.status === 'needs_reconnect'
                  ? 'Connect Spotify again'
                  : 'Connect Spotify'}
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
                Keep the current Client ID
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
                  Change Client ID
                </button>
                {dialog === 'change' ? (
                  <div className="notice" role="group" aria-label="Confirm Client ID change">
                    <p>{CHANGE_CLIENT_ID_TEXT}</p>
                    <button
                      className="button"
                      type="button"
                      onClick={() => {
                        setDialog(null)
                        setChanging(true)
                      }}
                    >
                      Continue
                    </button>
                    <button
                      className="button button--secondary"
                      type="button"
                      onClick={() => {
                        setDialog(null)
                      }}
                    >
                      Keep the current Client ID
                    </button>
                  </div>
                ) : null}
              </>
            ) : null}
            {dialog === 'disconnect' ? (
              <div className="notice" role="group" aria-label="Confirm disconnect">
                <p>{DISCONNECT_TEXT}</p>
                <button
                  className="button"
                  type="button"
                  disabled={action.busy}
                  onClick={() => {
                    run(
                      () => guard(() => disconnectSpotify()),
                      'Spotify is disconnected. classbeat no longer uses its sign-in.',
                    )
                  }}
                >
                  Yes, disconnect Spotify
                </button>
                <button
                  className="button button--secondary"
                  type="button"
                  onClick={() => {
                    setDialog(null)
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
                  setDialog('disconnect')
                }}
              >
                Disconnect Spotify
              </button>
            )}
          </>
        ) : null}
      </Section>

      {connected ? (
        <Section title="Working playlist">
          <blockquote data-testid="spotify-consent-text">{status.consentText}</blockquote>
          {status.poolConsent ? (
            <>
              <p data-testid="spotify-consent-state">You have allowed the working playlist.</p>
              {dialog === 'revoke' ? (
                <div className="notice" role="group" aria-label="Confirm revoke">
                  <p>{REVOKE_TEXT}</p>
                  <button
                    className="button"
                    type="button"
                    disabled={action.busy}
                    onClick={() => {
                      run(
                        () => guard(() => revokeSpotifyConsent()),
                        'Your consent is withdrawn. The playlist stays in your Spotify.',
                      )
                    }}
                  >
                    Yes, revoke my consent
                  </button>
                  <button
                    className="button button--secondary"
                    type="button"
                    onClick={() => {
                      setDialog(null)
                    }}
                  >
                    Keep my consent
                  </button>
                </div>
              ) : (
                <button
                  className="button button--secondary"
                  type="button"
                  disabled={action.busy}
                  onClick={() => {
                    setDialog('revoke')
                  }}
                >
                  Revoke consent
                </button>
              )}
            </>
          ) : (
            <>
              <p data-testid="spotify-consent-state">You have not allowed it yet.</p>
              <button
                className="button"
                type="button"
                disabled={action.busy}
                onClick={() => {
                  run(
                    () => guard(() => giveSpotifyConsent()),
                    'Thanks. classbeat may use the working playlist.',
                  )
                }}
              >
                I agree
              </button>
            </>
          )}
        </Section>
      ) : null}
    </div>
  )
}
