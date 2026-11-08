import { useEffect } from 'react'
import { login } from '../api/auth'
import { oauthOutcomeMessage } from '../api/messages'
import { setPhoneSignIn } from '../auth/phoneSignIn'
import { useSession } from '../auth/SessionContext'
import { passkeysSupported } from '../auth/webauthn'
import { useT } from '../i18n/I18n'
import { bestLocale, isLocale } from '../i18n/locales'
import { AuthScreen } from '../components/auth/AuthScreen'
import { safeReturnTo, useQuery, useRouter } from '../router/Router'

export function SignInPage() {
  const t = useT()
  const { navigate } = useRouter()
  const { refresh, session } = useSession()
  const query = useQuery()
  const outcome = query.get('oauth')
  const returnTo = safeReturnTo(query.get('return_to'))

  useEffect(() => {
    // A provider sign-in without a verified address: the address page finishes it.
    if (outcome === 'email_needed') navigate('/signin/email', { replace: true })
  }, [outcome, navigate])

  // After sign-in the account's language wins once, as it does for a provider sign-in (the
  // backend's unprefixed redirect); an account that follows the browser keeps the URL's
  // language (ADR 0027 section 3). `returnTo` stays language-neutral.
  const finish = async (role: string, crossPlatform: boolean) => {
    // Only the browser's exact "cross-platform" counts (ADR 0019, owner question 4).
    setPhoneSignIn(crossPlatform)
    const next = await refresh()
    const saved = next?.user?.locale ?? null
    const lang = saved !== null && isLocale(saved) ? bestLocale(next?.locales ?? [], saved) : undefined
    navigate(returnTo ?? (role === 'admin' ? '/admin' : '/account'), { lang })
  }

  return (
    <AuthScreen
      mode="signin"
      returnTo={returnTo}
      notice={
        outcome && outcome !== 'email_needed'
          ? oauthOutcomeMessage(outcome, undefined, query.get('provider'))
          : null
      }
      // A new provider account has no classbeat account: sign-up is where an invite creates one (#617).
      noticeLink={outcome === 'no_account' ? { to: '/signup', text: t('auth.createAccount') } : null}
      onPasskeySignedIn={
        session?.passkeys === true && passkeysSupported()
          ? ({ role, crossPlatform }) => finish(role, crossPlatform)
          : undefined
      }
      onSubmit={async ({ email, password, captchaToken }) => {
        const { role } = await login(email, password, captchaToken)
        await finish(role, false)
      }}
    />
  )
}
