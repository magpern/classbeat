import { useEffect, useRef, useState, type FormEvent } from 'react'
import type { SignInOutcome } from '../../auth/passkeyCeremony'
import { getOAuthProviders, startOAuth } from '../../api/auth'
import { providerName } from '../../api/messages'
import { Trans, useT } from '../../i18n/I18n'
import { goToProvider } from '../../lib/navigation'
import { Link } from '../../router/Router'
import { Field, Notice, useAction } from '../forms'
import { AuthCard } from './AuthCard'
import { PasskeySignIn } from './PasskeySignIn'
import { ProviderButton } from './ProviderButton'
import { useCaptcha } from './Turnstile'

export type AuthMode = 'signin' | 'signup'

interface Props {
  mode: AuthMode
  returnTo: string | null
  /** An error from a provider round trip (the `?oauth=` code), shown above the form. */
  notice?: string | null
  /** A link shown right under the notice (sign-in's "Create an account" for `no_account`). */
  noticeLink?: { to: string; text: string } | null
  /**
   * Passkey sign-in (ADR 0019 section 5): given only on the sign-in page, where the flag is on and
   * the browser has WebAuthn. Adds the button, and the `webauthn` autofill token to the email field.
   */
  onPasskeySignedIn?: (outcome: SignInOutcome) => Promise<void>
  /** The email and password path; throws on failure, which is shown in plain language. */
  onSubmit: (values: {
    email: string
    password: string
    invite: string
    captchaToken: string | null
  }) => Promise<void>
}

const COPY = {
  signin: {
    title: 'auth.signIn',
    submit: 'auth.signIn',
    autoComplete: 'current-password',
    swap: { to: '/signup', text: 'auth.newHere' },
  },
  signup: {
    title: 'auth.createAccount',
    submit: 'auth.createAccountSubmit',
    autoComplete: 'new-password',
    swap: { to: '/signin', text: 'auth.haveAccountSignIn' },
  },
} as const satisfies Record<AuthMode, unknown>

/**
 * One screen for Sign in and Sign up: the visible providers on top, an OR divider, email and
 * password, one primary button and a short legal line. The providers shown are the ones the
 * server lists (`oauth_visible_providers`); none listed means no buttons and no divider.
 * Sign up carries the beta invite code once, for both ways of creating the account.
 */
export function AuthScreen({ mode, returnTo, notice, noticeLink, onPasskeySignedIn, onSubmit }: Props) {
  const t = useT()
  const copy = COPY[mode]
  const [providers, setProviders] = useState<string[]>([])
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [invite, setInvite] = useState('')
  // Sign-up only: a provider was chosen with the invite field empty (#617). Nothing is sent.
  const [inviteMissing, setInviteMissing] = useState(false)
  const inviteRef = useRef<HTMLInputElement>(null)
  const action = useAction()
  // ADR 0025 section 1: the email and password form waits for the check; the provider buttons do not.
  const captcha = useCaptcha(mode)

  useEffect(() => {
    const controller = new AbortController()
    getOAuthProviders(controller.signal).then(setProviders, () => undefined)
    return () => {
      controller.abort()
    }
  }, [])

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!captcha.ready) return
    const captchaToken = captcha.token
    void action.run(async () => {
      try {
        await onSubmit({ email, password, invite, captchaToken })
      } catch (error) {
        // The token was sent, so it is spent whatever the answer: the check runs again.
        if (captchaToken !== null) captcha.reset()
        throw error
      }
    })
  }

  const start = (provider: string) => {
    const inviteCode = mode === 'signup' ? invite.trim() : ''
    if (mode === 'signup' && inviteCode === '') {
      // A provider sign-up needs the invite in the flow: stay on the page and say so.
      action.clear()
      setInviteMissing(true)
      inviteRef.current?.focus()
      return
    }
    setInviteMissing(false)
    void action.run(async () => {
      // Sign-in never creates an account and sends no invite; sign-up carries the invite.
      const url = await startOAuth(provider, mode === 'signup' ? 'sign_up' : 'sign_in', {
        inviteCode,
        returnTo: returnTo ?? undefined,
      })
      goToProvider(url)
    })
  }

  return (
    <AuthCard title={t(copy.title)}>
      {mode === 'signup' ? <p className="auth__lead">{t('auth.inviteLead')}</p> : null}
      {notice ? <Notice kind="error">{notice}</Notice> : null}
      {notice && noticeLink ? (
        <p className="card__links">
          <Link to={noticeLink.to}>{noticeLink.text}</Link>
        </p>
      ) : null}
      {inviteMissing ? <Notice kind="error">{t('auth.inviteFirst')}</Notice> : null}
      {action.error ? <Notice kind="error">{action.error}</Notice> : null}
      {mode === 'signup' ? (
        <Field
          ref={inviteRef}
          label={t('auth.inviteCode')}
          autoComplete="off"
          value={invite}
          required
          aria-invalid={inviteMissing ? true : undefined}
          onChange={(e) => {
            setInvite(e.target.value)
            if (e.target.value.trim() !== '') setInviteMissing(false)
          }}
        />
      ) : null}
      {providers.length > 0 || onPasskeySignedIn ? (
        <>
          <div className="auth__social">
            {onPasskeySignedIn ? <PasskeySignIn onSignedIn={onPasskeySignedIn} /> : null}
            {providers.map((provider) => (
              <ProviderButton
                key={provider}
                provider={provider}
                disabled={action.busy}
                onClick={() => {
                  start(provider)
                }}
              >
                {t('auth.continueWith', { provider: providerName(provider) })}
              </ProviderButton>
            ))}
          </div>
          <p className="auth__divider">
            <span>{t('auth.or')}</span>
          </p>
        </>
      ) : null}
      <form onSubmit={submit}>
        <Field
          label={t('common.email')}
          type="email"
          autoComplete={mode === 'signin' ? (onPasskeySignedIn ? 'username webauthn' : 'username') : 'email'}
          value={email}
          onChange={(e) => {
            setEmail(e.target.value)
          }}
          required
        />
        <Field
          label={t('common.password')}
          type="password"
          autoComplete={copy.autoComplete}
          hint={mode === 'signup' ? t('auth.passwordHint') : undefined}
          value={password}
          onChange={(e) => {
            setPassword(e.target.value)
          }}
          required
        />
        {captcha.element}
        <button
          className="button button--block"
          type="submit"
          disabled={action.busy || !captcha.ready}
          aria-describedby={captcha.describedBy}
        >
          {t(copy.submit)}
        </button>
      </form>
      <p className="auth__legal">
        {t('auth.legal')}{' '}
        <Trans
          id="legal.read"
          slots={{
            privacy: <Link to="/privacy">{t('legal.inline.privacy')}</Link>,
            terms: <Link to="/terms">{t('legal.inline.terms')}</Link>,
          }}
        />
      </p>
      <p className="card__links">
        <Link to={copy.swap.to}>{t(copy.swap.text)}</Link>
        {mode === 'signin' ? <Link to="/reset">{t('auth.forgot')}</Link> : null}
      </p>
    </AuthCard>
  )
}
