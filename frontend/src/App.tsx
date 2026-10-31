import { GuardProvider } from './auth/GuardProvider'
import { SessionProvider, useSession } from './auth/SessionContext'
import { AppShell } from './components/shell/AppShell'
import { AccountPage } from './pages/AccountPage'
import { AdminPage } from './pages/AdminPage'
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

function Routes() {
  const { pathname } = useRouter()
  const { session } = useSession()
  const path = pathname.replace(/\/+$/, '') || '/'
  const job = /^\/jobs\/([0-9a-fA-F-]{36})$/.exec(path)
  if (job?.[1] !== undefined) return <JobPage key={job[1]} jobId={job[1]} />
  const cls = /^\/classes\/([0-9a-fA-F-]{36})$/.exec(path)
  if (cls?.[1] !== undefined) return <ClassPage key={cls[1]} classId={cls[1]} />
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

export function App() {
  return (
    <RouterProvider>
      <SessionProvider>
        <GuardProvider>
          <AppShell>
            <Routes />
          </AppShell>
        </GuardProvider>
      </SessionProvider>
    </RouterProvider>
  )
}
