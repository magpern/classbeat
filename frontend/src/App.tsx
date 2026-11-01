import { lazy, Suspense } from 'react'
import { GuardProvider } from './auth/GuardProvider'
import { SessionProvider, useSession } from './auth/SessionContext'
import { Banner } from './components/Banner'
import { Card } from './components/forms'
import { RequireAuth } from './components/RequireAuth'
import { AppShell } from './components/shell/AppShell'
import { AccountPage } from './pages/AccountPage'
import { ClassPage } from './pages/ClassPage'
import { ClassesPage } from './pages/ClassesPage'
import { EmailChangePage } from './pages/EmailChangePage'
import { JobPage } from './pages/JobPage'
import { HomePage } from './pages/HomePage'
import { LandingPage } from './pages/LandingPage'
import { MusicPage } from './pages/MusicPage'
import { NewClassPage } from './pages/NewClassPage'
import { ProviderEmailPage } from './pages/ProviderEmailPage'
import { ResetPage } from './pages/ResetPage'
import { SignInPage } from './pages/SignInPage'
import { SignUpConfirmPage } from './pages/SignUpConfirmPage'
import { SignUpPage } from './pages/SignUpPage'
import { SpotifyPage } from './pages/SpotifyPage'
import { Link, RouterProvider, useRouter } from './router/Router'

// The admin area is its own chunk, fetched only for a session whose role is admin or whose
// account is an admin one with its TOTP check missing or lapsed (`adminPendingMfa`, ADR 0011
// section 6); the server's answer decides what it shows, and the chunk's gate shows the code
// prompt. Any other account never fetches it.
const AdminPage = lazy(() => import('./pages/AdminPage'))

function Admin() {
  const { session } = useSession()
  return (
    <RequireAuth>
      {session?.user?.role === 'admin' || session?.session?.adminPendingMfa === true ? (
        <Suspense
          fallback={
            <Card title="Admin">
              <p>Loading</p>
            </Card>
          }
        >
          <AdminPage />
        </Suspense>
      ) : (
        <Card title="Admin">
          <p role="alert" className="notice" data-kind="error">
            Your account is not an admin account.
          </p>
        </Card>
      )}
    </RequireAuth>
  )
}

function Routes() {
  const { pathname } = useRouter()
  const { session } = useSession()
  const path = pathname.replace(/\/+$/, '') || '/'
  const job = /^\/jobs\/([0-9a-fA-F-]{36})$/.exec(path)
  if (job?.[1] !== undefined) return <JobPage key={job[1]} jobId={job[1]} />
  const cls = /^\/classes\/([0-9a-fA-F-]{36})$/.exec(path)
  if (cls?.[1] !== undefined) return <ClassPage key={cls[1]} classId={cls[1]} />
  if (path === '/admin' || path.startsWith('/admin/')) return <Admin />
  switch (path) {
    case '/':
      return session?.authenticated === true ? <HomePage /> : <LandingPage />
    case '/signin':
      return <SignInPage />
    case '/signin/email':
      return <ProviderEmailPage />
    case '/signup':
      return <SignUpPage />
    case '/signup/confirm':
      return <SignUpConfirmPage />
    case '/reset':
      return <ResetPage />
    case '/email-change':
      return <EmailChangePage />
    case '/classes':
      return <ClassesPage />
    case '/classes/new':
      return <NewClassPage />
    case '/music':
      return <MusicPage />
    case '/account':
      return <AccountPage />
    case '/account/spotify':
      return <SpotifyPage />
    default:
      return (
        <section className="card">
          <h1 className="card__title">Page not found</h1>
          <p>
            <Link to="/">Back to the start</Link>
          </p>
        </section>
      )
  }
}

export function App() {
  return (
    <RouterProvider>
      <SessionProvider>
        <GuardProvider>
          <Banner />
          <AppShell>
            <Routes />
          </AppShell>
        </GuardProvider>
      </SessionProvider>
    </RouterProvider>
  )
}
