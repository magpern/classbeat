import { useEffect } from 'react'
import { login } from '../api/auth'
import { oauthOutcomeMessage } from '../api/messages'
import { useSession } from '../auth/SessionContext'
import { AuthScreen } from '../components/auth/AuthScreen'
import { safeReturnTo, useQuery, useRouter } from '../router/Router'

export function SignInPage() {
  const { navigate } = useRouter()
  const { refresh } = useSession()
  const query = useQuery()
  const outcome = query.get('oauth')
  const returnTo = safeReturnTo(query.get('return_to'))

  useEffect(() => {
    // A provider sign-in without a verified address: the address page finishes it.
    if (outcome === 'email_needed') navigate('/signin/email', { replace: true })
  }, [outcome, navigate])

  return (
    <AuthScreen
      mode="signin"
      returnTo={returnTo}
      notice={outcome && outcome !== 'email_needed' ? oauthOutcomeMessage(outcome) : null}
      onSubmit={async ({ email, password }) => {
        const { role } = await login(email, password)
        await refresh()
        navigate(returnTo ?? (role === 'admin' ? '/admin' : '/account'))
      }}
    />
  )
}
