import { useEffect, useState, type FormEvent } from 'react'
import { login } from '../api/auth'
import { oauthOutcomeMessage } from '../api/messages'
import { useSession } from '../auth/SessionContext'
import { Field, Notice, useAction } from '../components/forms'
import { AuthCard as Card } from '../components/auth/AuthCard'
import { ProviderButtons } from '../components/ProviderButtons'
import { Link, safeReturnTo, useQuery, useRouter } from '../router/Router'

export function SignInPage() {
  const { navigate } = useRouter()
  const { refresh } = useSession()
  const query = useQuery()
  const outcome = query.get('oauth')
  const returnTo = safeReturnTo(query.get('return_to'))
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const action = useAction()

  useEffect(() => {
    // A provider sign-in without a verified address: the address page finishes it.
    if (outcome === 'email_needed') navigate('/signin/email', { replace: true })
  }, [outcome, navigate])

  const submit = (event: FormEvent) => {
    event.preventDefault()
    void action.run(async () => {
      const { role } = await login(email, password)
      await refresh()
      navigate(returnTo ?? (role === 'admin' ? '/admin' : '/account'))
    })
  }

  return (
    <Card title="Sign in">
      {outcome && outcome !== 'email_needed' ? (
        <Notice kind="error">{oauthOutcomeMessage(outcome)}</Notice>
      ) : null}
      {action.error ? <Notice kind="error">{action.error}</Notice> : null}
      <form onSubmit={submit}>
        <Field
          label="Email"
          type="email"
          autoComplete="username"
          value={email}
          onChange={(e) => {
            setEmail(e.target.value)
          }}
          required
        />
        <Field
          label="Password"
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(e) => {
            setPassword(e.target.value)
          }}
          required
        />
        <button className="button" type="submit" disabled={action.busy}>
          Sign in
        </button>
      </form>
      <ProviderButtons returnTo={returnTo} />
      <p className="card__links">
        <Link to="/reset">Forgot your password?</Link>
        <Link to="/signup">Create an account</Link>
      </p>
    </Card>
  )
}
