import { useEffect, useState, type FormEvent } from 'react'
import { getOAuthProviders, startOAuth } from '../../api/auth'
import { providerName } from '../../api/messages'
import { goToProvider } from '../../lib/navigation'
import { Link } from '../../router/Router'
import { Field, Notice, useAction } from '../forms'
import { AuthCard } from './AuthCard'

export type AuthMode = 'signin' | 'signup'

interface Props {
  mode: AuthMode
  returnTo: string | null
  /** An error from a provider round trip (the `?oauth=` code), shown above the form. */
  notice?: string | null
  /** The email and password path; throws on failure, which is shown in plain language. */
  onSubmit: (values: { email: string; password: string; invite: string }) => Promise<void>
}

const COPY = {
  signin: {
    title: 'Sign in',
    submit: 'Sign in',
    autoComplete: 'current-password',
    swap: { to: '/signup', text: 'New here? Create an account' },
  },
  signup: {
    title: 'Create an account',
    submit: 'Create account',
    autoComplete: 'new-password',
    swap: { to: '/signin', text: 'Already have an account? Sign in' },
  },
} as const

/**
 * One screen for Sign in and Sign up: the visible providers on top, an OR divider, email and
 * password, one primary button and a short legal line. The providers shown are the ones the
 * server lists (`oauth_visible_providers`); none listed means no buttons and no divider.
 * Sign up carries the beta invite code once, for both ways of creating the account.
 */
export function AuthScreen({ mode, returnTo, notice, onSubmit }: Props) {
  const copy = COPY[mode]
  const [providers, setProviders] = useState<string[]>([])
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [invite, setInvite] = useState('')
  const action = useAction()

  useEffect(() => {
    const controller = new AbortController()
    getOAuthProviders(controller.signal).then(setProviders, () => undefined)
    return () => {
      controller.abort()
    }
  }, [])

  const submit = (event: FormEvent) => {
    event.preventDefault()
    void action.run(() => onSubmit({ email, password, invite }))
  }

  const start = (provider: string) => {
    void action.run(async () => {
      const url = await startOAuth(provider, 'sign_in', {
        inviteCode: mode === 'signup' ? invite.trim() : '',
        returnTo: returnTo ?? undefined,
      })
      goToProvider(url)
    })
  }

  return (
    <AuthCard title={copy.title}>
      {mode === 'signup' ? (
        <p className="auth__lead">classbeat is invite-only for now. Enter the invite code you were given.</p>
      ) : null}
      {notice ? <Notice kind="error">{notice}</Notice> : null}
      {action.error ? <Notice kind="error">{action.error}</Notice> : null}
      {mode === 'signup' ? (
        <Field
          label="Invite code"
          autoComplete="off"
          value={invite}
          onChange={(e) => {
            setInvite(e.target.value)
          }}
        />
      ) : null}
      {providers.length > 0 ? (
        <>
          <div className="auth__social">
            {providers.map((provider) => (
              <button
                key={provider}
                type="button"
                className="button button--secondary button--block"
                disabled={action.busy}
                onClick={() => {
                  start(provider)
                }}
              >
                Continue with {providerName(provider)}
              </button>
            ))}
          </div>
          <p className="auth__divider">
            <span>OR</span>
          </p>
        </>
      ) : null}
      <form onSubmit={submit}>
        <Field
          label="Email"
          type="email"
          autoComplete={mode === 'signin' ? 'username' : 'email'}
          value={email}
          onChange={(e) => {
            setEmail(e.target.value)
          }}
          required
        />
        <Field
          label="Password"
          type="password"
          autoComplete={copy.autoComplete}
          hint={mode === 'signup' ? '12 to 128 characters. You will type it again when you confirm.' : undefined}
          value={password}
          onChange={(e) => {
            setPassword(e.target.value)
          }}
          required
        />
        <button className="button button--block" type="submit" disabled={action.busy}>
          {copy.submit}
        </button>
      </form>
      <p className="auth__legal">
        By continuing you agree to take part in the invite-only beta. classbeat is not made by or
        affiliated with MOWL, Spotify or Swedebeat.
      </p>
      <p className="card__links">
        <Link to={copy.swap.to}>{copy.swap.text}</Link>
        {mode === 'signin' ? <Link to="/reset">Forgot your password?</Link> : null}
      </p>
    </AuthCard>
  )
}
