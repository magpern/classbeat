import { useEffect } from 'react'
import { login } from '../api/auth'
import { oauthOutcomeMessage } from '../api/messages'
import { useSession } from '../auth/SessionContext'
import { useT } from '../i18n/I18n'
import { bestLocale, isLocale } from '../i18n/locales'
import { AuthScreen } from '../components/auth/AuthScreen'
import { safeReturnTo, useQuery, useRouter } from '../router/Router'

export function SignInPage() {
  const t = useT()
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
      notice={
        outcome && outcome !== 'email_needed'
          ? oauthOutcomeMessage(outcome, undefined, query.get('provider'))
          : null
      }
      // A new provider account has no classbeat account: sign-up is where an invite creates one (#617).
      noticeLink={outcome === 'no_account' ? { to: '/signup', text: t('auth.createAccount') } : null}
      onSubmit={async ({ email, password, captchaToken }) => {
        const { role } = await login(email, password, captchaToken)
        const next = await refresh()
        // After sign-in the account's language wins once, as it does for a provider sign-in (the
        // backend's unprefixed redirect); an account that follows the browser keeps the URL's
        // language (ADR 0027 section 3). `returnTo` stays language-neutral.
        const saved = next?.user?.locale ?? null
        const lang = saved !== null && isLocale(saved) ? bestLocale(next?.locales ?? [], saved) : undefined
        navigate(returnTo ?? (role === 'admin' ? '/admin' : '/account'), { lang })
      }}
    />
  )
}
