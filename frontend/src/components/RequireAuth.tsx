import type { ReactNode } from 'react'
import { useSession } from '../auth/SessionContext'
import { Link, useRouter } from '../router/Router'
import { Card, Notice } from './forms'

/** Renders its children only for a signed-in user; others are pointed to sign-in. */
export function RequireAuth({ children }: { children: ReactNode }) {
  const { status, session } = useSession()
  const { pathname } = useRouter()
  if (status === 'loading') {
    return (
      <Card title="One moment">
        <p>Loading</p>
      </Card>
    )
  }
  if (status === 'error') {
    return (
      <Card title="Cannot reach the server">
        <Notice kind="error">Try again in a moment.</Notice>
      </Card>
    )
  }
  if (!session?.authenticated) {
    return (
      <Card title="Sign in first">
        <p>This page is for signed-in users.</p>
        <p>
          <Link className="button" to={`/signin?return_to=${encodeURIComponent(pathname)}`}>
            Sign in
          </Link>
        </p>
      </Card>
    )
  }
  return <>{children}</>
}
