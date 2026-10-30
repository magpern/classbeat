import { logout, logoutAll } from '../api/auth'
import { oauthOutcomeMessage } from '../api/messages'
import { useSession } from '../auth/SessionContext'
import { Connections } from '../components/account/Connections'
import { ChangeEmail, ChangePassword } from '../components/account/Credentials'
import { SpotifySummary } from '../components/account/Spotify'
import { SignInMethods } from '../components/account/SignInMethods'
import { TwoFactor } from '../components/account/TwoFactor'
import { Usage } from '../components/account/Usage'
import { Notice, Section, useAction } from '../components/forms'
import { RequireAuth } from '../components/RequireAuth'
import { useQuery, useRouter } from '../router/Router'

const OAUTH_NOTES: Record<string, string> = {
  linked: 'The sign-in method is linked to your account.',
  reauthed: 'Thanks, you are confirmed. Repeat the change you were making.',
}

export function AccountPage() {
  return (
    <RequireAuth>
      <Account />
    </RequireAuth>
  )
}

function Account() {
  const { session, refresh } = useSession()
  const { navigate } = useRouter()
  const outcome = useQuery().get('oauth')
  const action = useAction()
  const email = session?.user?.email ?? ''

  const signOut = (everywhere: boolean) => {
    void action.run(async () => {
      await (everywhere ? logoutAll() : logout())
      await refresh()
      navigate('/signin')
    })
  }

  return (
    <div className="stack">
      <h1>Your account</h1>
      {outcome ? (
        Object.hasOwn(OAUTH_NOTES, outcome) ? (
          <Notice kind="success">{OAUTH_NOTES[outcome]}</Notice>
        ) : (
          <Notice kind="error">{oauthOutcomeMessage(outcome)}</Notice>
        )
      ) : null}
      <Connections />
      <SpotifySummary />
      <Usage />
      <SignInMethods />
      <ChangeEmail current={email} />
      <ChangePassword />
      <TwoFactor />
      <Section title="Sessions">
        {action.error ? <Notice kind="error">{action.error}</Notice> : null}
        <button
          className="button button--secondary"
          type="button"
          disabled={action.busy}
          onClick={() => {
            signOut(false)
          }}
        >
          Sign out
        </button>
        <button
          className="button button--secondary"
          type="button"
          disabled={action.busy}
          onClick={() => {
            signOut(true)
          }}
        >
          Sign out everywhere
        </button>
      </Section>
    </div>
  )
}
