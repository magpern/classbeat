import { useState, type FormEvent } from 'react'
import type { SpotifyStatus } from '../../api/spotify'
import { useT } from '../../i18n/I18n'
import { Field, Notice } from '../forms'

// ADR 0009 section 2: the dashboard steps, the fixed callback URL and the one field, the Client
// ID. No field asks for a Client Secret: Spotify sign-in is PKCE. An invalid Client ID is blocked
// here, nothing is sent, and the value is not repeated back.

const CLIENT_ID = /^[0-9a-fA-F]{32}$/

/** The Client ID step of the Spotify wizard; `onStart` gets a trimmed, valid Client ID. */
export function SpotifyWizard({
  status,
  confirmSwitch,
  changing,
  busy,
  onStart,
  onKeep,
}: {
  status: SpotifyStatus
  /** After `account_changed`: the next start carries the user's confirmation. */
  confirmSwitch: boolean
  /** A connected user is changing the Client ID (after the confirmation). */
  changing: boolean
  busy: boolean
  onStart: (clientId: string) => void
  onKeep: () => void
}) {
  const t = useT()
  const [clientId, setClientId] = useState('')
  const [invalid, setInvalid] = useState(false)
  const [copy, setCopy] = useState<'copied' | 'failed' | null>(null)

  const connect = (event: FormEvent) => {
    event.preventDefault()
    const sent = clientId.trim()
    if (!CLIENT_ID.test(sent)) {
      setInvalid(true)
      return
    }
    setInvalid(false)
    onStart(sent)
  }

  const copyUrl = (url: string) => {
    try {
      navigator.clipboard.writeText(url).then(
        () => {
          setCopy('copied')
        },
        () => {
          setCopy('failed')
        },
      )
    } catch {
      setCopy('failed') // no clipboard in this browser context
    }
  }

  return (
    <form onSubmit={connect} noValidate className="flex flex-col gap-3">
      <p className="m-0">{t('spot.intro')}</p>
      <ol className="m-0 flex flex-col gap-2 pl-5 wrap-anywhere">
        <li>{t('spot.step1')}</li>
        <li>
          {t('spot.step2')}
          <div className="field">
            <code data-testid="spotify-callback-url" className="break-all">
              {status.callbackUrl}
            </code>{' '}
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
            {copy === 'failed' ? <span role="status">{t('spot.copyFailed')}</span> : null}
          </div>
        </li>
        <li>{t('spot.step3')}</li>
        <li>{t('spot.step4')}</li>
        <li>{t('spot.step5')}</li>
      </ol>
      {changing || confirmSwitch ? (
        <p className="m-0 text-muted">{confirmSwitch ? t('spot.confirmHint') : t('spot.changeText')}</p>
      ) : null}
      <Field
        label={t('spot.clientId')}
        type="text"
        autoComplete="off"
        autoCapitalize="off"
        spellCheck={false}
        className="w-full min-w-0"
        value={clientId}
        aria-invalid={invalid}
        onChange={(e) => {
          setClientId(e.target.value)
          setInvalid(false)
        }}
      />
      {invalid ? <Notice kind="error">{t('spot.invalidClientId')}</Notice> : null}
      <div className="flex flex-wrap gap-3">
        <button className="button" type="submit" disabled={busy}>
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
              setInvalid(false)
              onKeep()
            }}
          >
            {t('spot.keepId')}
          </button>
        ) : null}
      </div>
    </form>
  )
}
