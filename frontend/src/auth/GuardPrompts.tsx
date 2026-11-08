import { useEffect, useState, type FormEvent } from 'react'
import {
  getIdentities,
  reauthWithPassword,
  reauthWithTotp,
  startOAuth,
  stepUp,
  type IdentitiesState,
} from '../api/auth'
import { providerName } from '../api/messages'
import { PasskeyReauth } from '../components/auth/PasskeyReauth'
import { Field, Notice, useAction } from '../components/forms'
import { useLocale, useT } from '../i18n/I18n'
import { goToProvider } from '../lib/navigation'
import { reauthReturnTo } from './prompts'
import { useSession } from './SessionContext'

// The two prompts the guard shows (ADR 0003 section 5): confirm it is you, and the TOTP step-up.

export interface PromptProps {
  onDone: (success: boolean) => void
}

export function ReauthPrompt({ onDone }: PromptProps) {
  const t = useT()
  const { locale } = useLocale()
  const { session } = useSession()
  const [state, setState] = useState<IdentitiesState | null>(null)
  const [password, setPassword] = useState('')
  const [code, setCode] = useState('')
  const action = useAction()
  const isAdmin = session?.user?.role === 'admin'

  useEffect(() => {
    const controller = new AbortController()
    getIdentities(controller.signal).then(setState, () => undefined)
    return () => {
      controller.abort()
    }
  }, [])

  const submitPassword = (event: FormEvent) => {
    event.preventDefault()
    void action.run(async () => {
      await reauthWithPassword(password)
      onDone(true)
    })
  }
  const submitCode = (event: FormEvent) => {
    event.preventDefault()
    void action.run(async () => {
      await reauthWithTotp(code)
      onDone(true)
    })
  }
  const viaProvider = (provider: string) => {
    void action.run(async () => {
      const url = await startOAuth(provider, 'reauth', {
        returnTo: reauthReturnTo(window.location.pathname, window.location.search, window.location.hash),
      })
      goToProvider(url)
    })
  }

  // Google sends no fresh auth_time (it ignores max_age=0), so it confirms only an account
  // without a password; one with a password confirms with it, and the server refuses Google for it
  // too (ADR 0003 section 5, #687). Microsoft proves a new sign-in only with an auth_time of the
  // last 5 minutes (#522), so the prompt says so before the user goes.
  const linked = (state?.identities.map((item) => item.provider) ?? []).filter(
    (provider) => !(provider === 'google' && state?.hasPassword !== false),
  )
  const timed = linked.filter((provider) => provider === 'microsoft')
  return (
    <>
      <h2 id="prompt-title">{t('guard.reauthTitle')}</h2>
      <p>{t('guard.reauthIntro')}</p>
      {action.error ? <Notice kind="error">{action.error}</Notice> : null}
      {state?.hasPassword ? (
        <form onSubmit={submitPassword}>
          <Field
            label={t('common.password')}
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => {
              setPassword(e.target.value)
            }}
            required
          />
          <button className="button" type="submit" disabled={action.busy}>
            {t('guard.confirmPassword')}
          </button>
        </form>
      ) : null}
      {isAdmin ? (
        <form onSubmit={submitCode}>
          <Field
            label={t('guard.code')}
            autoComplete="one-time-code"
            value={code}
            onChange={(e) => {
              setCode(e.target.value)
            }}
            required
          />
          <button className="button" type="submit" disabled={action.busy}>
            {t('guard.confirmCode')}
          </button>
        </form>
      ) : null}
      <PasskeyReauth action={action} onDone={onDone} />
      {linked.map((provider) => (
        <button
          key={provider}
          className="button button--secondary"
          type="button"
          disabled={action.busy}
          onClick={() => {
            viaProvider(provider)
          }}
        >
          {t('guard.confirmProvider', { provider: providerName(provider) })}
        </button>
      ))}
      {timed.length > 0 ? (
        <p className="text-sm text-muted">
          {t(state?.hasPassword ? 'guard.oidcPassword' : 'guard.oidc', {
            providers: new Intl.ListFormat(locale, {
              type: 'conjunction',
            }).format(timed.map(providerName)),
          })}
        </p>
      ) : null}
      <button
        className="button button--link"
        type="button"
        onClick={() => {
          onDone(false)
        }}
      >
        {t('common.cancel')}
      </button>
    </>
  )
}

export function StepUpPrompt({ onDone }: PromptProps) {
  const t = useT()
  const [code, setCode] = useState('')
  const action = useAction()
  const submit = (event: FormEvent) => {
    event.preventDefault()
    void action.run(async () => {
      await stepUp(code)
      onDone(true)
    })
  }
  return (
    <>
      <h2 id="prompt-title">{t('guard.stepUpTitle')}</h2>
      <p>{t('guard.stepUpIntro')}</p>
      {action.error ? <Notice kind="error">{action.error}</Notice> : null}
      <form onSubmit={submit}>
        <Field
          label={t('guard.code')}
          autoComplete="one-time-code"
          inputMode="text"
          value={code}
          onChange={(e) => {
            setCode(e.target.value)
          }}
          required
        />
        <button className="button" type="submit" disabled={action.busy}>
          {t('common.continue')}
        </button>
      </form>
      <button
        className="button button--link"
        type="button"
        onClick={() => {
          onDone(false)
        }}
      >
        {t('common.cancel')}
      </button>
    </>
  )
}
