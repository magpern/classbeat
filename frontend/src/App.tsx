import { logout } from './api/auth'
import { GuardProvider } from './auth/GuardProvider'
import { SessionProvider, useSession } from './auth/SessionContext'
import { ApiStatus } from './components/ApiStatus'
import { Logo } from './components/Logo'
import { AccountPage } from './pages/AccountPage'
import { AdminPage } from './pages/AdminPage'
import { EmailChangePage } from './pages/EmailChangePage'
import { LandingPage } from './pages/LandingPage'
import { ProviderEmailPage } from './pages/ProviderEmailPage'
import { ResetPage } from './pages/ResetPage'
import { SignInPage } from './pages/SignInPage'
import { SignUpConfirmPage } from './pages/SignUpConfirmPage'
import { SignUpPage } from './pages/SignUpPage'
import { Link, RouterProvider, useRouter } from './router/Router'

function Routes() {
  const { pathname } = useRouter()
  switch (pathname.replace(/\/+$/, '') || '/') {
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
    case '/account':
      return <AccountPage />
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
    void logout()
      .then(refresh)
      .then(() => {
        navigate('/signin')
      })
  }
  return (
    <nav className="nav" aria-label="Account">
      <Link to="/account">{session.user?.email}</Link>
      <button type="button" className="button button--link" onClick={signOut}>
        Sign out
      </button>
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
