import type { ReactNode } from 'react'
import { useSession } from '../auth/SessionContext'
import { useT } from '../i18n/I18n'
import { Link, useRouter } from '../router/Router'
import { Card, Notice } from './forms'

/** Renders its children only for a signed-in user; others are pointed to sign-in. */
export function RequireAuth({ children }: { children: ReactNode }) {
  const t = useT()
  const { status, session } = useSession()
  const { pathname } = useRouter()
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
    return (
      <Card title={t('shell.requireAuth.title')}>
        <p>{t('shell.requireAuth.text')}</p>
        <p>
          <Link className="button" to={`/signin?return_to=${encodeURIComponent(pathname)}`}>
            {t('auth.signIn')}
          </Link>
        </p>
      </Card>
    )
  }
  return <>{children}</>
}
