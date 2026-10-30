import { logout } from './api/auth'
import { GuardProvider } from './auth/GuardProvider'
import { SessionProvider, useSession } from './auth/SessionContext'
import { ApiStatus } from './components/ApiStatus'
import { useAction } from './components/forms'
import { Logo } from './components/Logo'
import { AccountPage } from './pages/AccountPage'
import { AdminPage } from './pages/AdminPage'
import { ClassPage } from './pages/ClassPage'
import { ClassesPage } from './pages/ClassesPage'
import { EmailChangePage } from './pages/EmailChangePage'
import { JobPage } from './pages/JobPage'
import { LandingPage } from './pages/LandingPage'
import { NewClassPage } from './pages/NewClassPage'
import { ProviderEmailPage } from './pages/ProviderEmailPage'
import { ResetPage } from './pages/ResetPage'
import { SignInPage } from './pages/SignInPage'
import { SignUpConfirmPage } from './pages/SignUpConfirmPage'
import { SignUpPage } from './pages/SignUpPage'
import { SpotifyPage } from './pages/SpotifyPage'
import { Link, RouterProvider, useRouter } from './router/Router'

function Routes() {
  const { pathname } = useRouter()
  const path = pathname.replace(/\/+$/, '') || '/'
  const job = /^\/jobs\/([0-9a-fA-F-]{36})$/.exec(path)
  if (job?.[1] !== undefined) return <JobPage key={job[1]} jobId={job[1]} />
  const cls = /^\/classes\/([0-9a-fA-F-]{36})$/.exec(path)
  if (cls?.[1] !== undefined) return <ClassPage key={cls[1]} classId={cls[1]} />
  switch (path) {
    case '/':
      return <LandingPage />
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
    case '/account':
      return <AccountPage />
    case '/account/spotify':
      return <SpotifyPage />
    case '/admin':
      return <AdminPage />
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

function Nav() {
  const { status, session, refresh } = useSession()
  const { navigate } = useRouter()
  const action = useAction()
  if (status !== 'ready' || session === null) return null
  if (!session.authenticated) {
    return (
      <nav className="nav" aria-label="Account">
        <Link to="/signin">Sign in</Link>
        <Link to="/signup">Sign up</Link>
      </nav>
    )
  }
  const signOut = () => {
    void action.run(async () => {
      await logout()
      await refresh()
      navigate('/signin')
    })
  }
  return (
    <nav className="nav" aria-label="Account">
      <Link to="/classes">My classes</Link>
      <Link to="/classes/new">New class</Link>
      <Link to="/account">{session.user?.email}</Link>
      <button type="button" className="button button--link" disabled={action.busy} onClick={signOut}>
        Sign out
      </button>
      {action.error ? <span role="alert">{action.error}</span> : null}
    </nav>
  )
}

export function App() {
  return (
    <RouterProvider>
      <SessionProvider>
        <GuardProvider>
          <header className="app-header">
            <div className="app-header__inner">
              <Link to="/" className="brand">
                <Logo />
                <span className="brand__name">classbeat</span>
              </Link>
              <Nav />
              <ApiStatus />
            </div>
          </header>
          <main className="app-main">
            <Routes />
          </main>
        </GuardProvider>
      </SessionProvider>
    </RouterProvider>
  )
}
