import { useEffect, type ReactNode } from 'react'
import { useSession } from '../auth/SessionContext'
import { useT } from '../i18n/I18n'
import { returnToWithExample, rememberExample, validExample } from '../lib/exampleHandoff'
import { Link, useQuery, useRouter } from '../router/Router'
import { Card, Notice } from './forms'

/** Renders its children only for a signed-in user; others are pointed to sign-in. */
export function RequireAuth({ children }: { children: ReactNode }) {
  const t = useT()
  const { status, session } = useSession()
  const { path, search } = useRouter()
  const example = validExample(useQuery().get('example'))
  const signedOut = status === 'ready' && !session?.authenticated
  // An example link (PP6) outlives e-mail sign-up, which `return_to` cannot carry: keep the slug for a day.
  useEffect(() => {
    if (signedOut && example !== null) rememberExample(example)
  }, [signedOut, example])
  if (status === 'loading') {
    return (
      <Card title={t('shell.requireAuth.loadingTitle')}>
        <p>{t('common.loading')}</p>
      </Card>
    )
  }
  if (status === 'error') {
    return (
      <Card title={t('shell.requireAuth.errorTitle')}>
        <Notice kind="error">{t('shell.requireAuth.errorTryAgain')}</Notice>
      </Card>
    )
  }
  if (!session?.authenticated) {
    const returnTo = encodeURIComponent(returnToWithExample(path, search))
    return (
      <Card title={t('shell.requireAuth.title')}>
        <p>{t('shell.requireAuth.text')}</p>
        <p className="flex flex-wrap gap-3">
          <Link className="button" to={`/signin?return_to=${returnTo}`}>
            {t('auth.signIn')}
          </Link>
          <Link className="button" to={`/signup?return_to=${returnTo}`}>
            {t('landing.signUp')}
          </Link>
        </p>
      </Card>
    )
  }
  return <>{children}</>
}
