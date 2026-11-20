import { lazy, Suspense, type ReactNode } from 'react'
import { AccountLocale } from './auth/AccountLocale'
import { GuardProvider } from './auth/GuardProvider'
import { SessionProvider, useSession } from './auth/SessionContext'
import { Banner } from './components/Banner'
import { CookieNotice } from './components/CookieNotice'
import { Card } from './components/forms'
import { RequireAuth } from './components/RequireAuth'
import { AppShell } from './components/shell/AppShell'
import { useT } from './i18n/I18n'
import { EmailChangePage } from './pages/EmailChangePage'
import { EmailRevertPage } from './pages/EmailRevertPage'
import { UnsubscribePage } from './pages/UnsubscribePage'
import { LandingPage } from './pages/LandingPage'
import { HelpPage } from './pages/HelpPage'
import { ExamplePage } from './pages/ExamplePage'
import { ExamplesPage } from './pages/ExamplesPage'
import { HowItWorksPage } from './pages/HowItWorksPage'
import { FeaturesPage } from './pages/FeaturesPage'
import { PricingPage } from './pages/PricingPage'
import { PrivacyDraftPage, RefundsDraftPage, SubProcessorsDraftPage, TermsDraftPage } from './pages/LegalDraftPages'
import { PrivacyPage } from './pages/PrivacyPage'
import { PlayerConsentPage } from './pages/PlayerConsentPage'
import { ProviderEmailPage } from './pages/ProviderEmailPage'
import { ResetPage } from './pages/ResetPage'
import { SignInPage } from './pages/SignInPage'
import { SignUpConfirmPage } from './pages/SignUpConfirmPage'
import { SignUpPage } from './pages/SignUpPage'
import { TermsPage } from './pages/TermsPage'
import { EXAMPLES } from './publicSite/examples'
import { hasUnknownLanguage, isAdminPath } from './router/paths'
import { Link, RouterProvider, useRouter, type Location } from './router/Router'

// Pages that are not prerendered load as their own chunks, so the first load stays small (#1072).
// The prerendered public pages stay in the entry (the takeover replaces them in one render), and so
// do the sign-in, sign-up and mail-link pages: a mail link's token is stripped from the address bar on mount.
const ClassPage = lazy(() => import('./pages/ClassPage').then((m) => ({ default: m.ClassPage })))
const ClassesPage = lazy(() => import('./pages/ClassesPage').then((m) => ({ default: m.ClassesPage })))
const NewClassPage = lazy(() => import('./pages/NewClassPage').then((m) => ({ default: m.NewClassPage })))
const JobPage = lazy(() => import('./pages/JobPage').then((m) => ({ default: m.JobPage })))
const HomePage = lazy(() => import('./pages/HomePage').then((m) => ({ default: m.HomePage })))
const MusicPage = lazy(() => import('./pages/MusicPage').then((m) => ({ default: m.MusicPage })))

// The admin area is its own chunk, fetched only for a session whose role is admin or whose
// account is an admin one with its TOTP check missing or lapsed (`adminPendingMfa`, ADR 0011
// section 6); the server's answer decides what it shows, and the chunk's gate shows the code
// prompt. Any other account never fetches it.
const AccountPageChunk = lazy(() => import('./pages/AccountPage').then((m) => ({ default: m.AccountPage })))
const AdminPage = lazy(() => import('./pages/AdminPage'))
// The Advanced form is fetched only on its own route.
const AdvancedClassPage = lazy(() => import('./pages/AdvancedClassPage'))

function Advanced() {
  const t = useT()
  return (
    <Suspense fallback={<p>{t('common.loading')}</p>}>
      <AdvancedClassPage />
    </Suspense>
  )
}

function Account() {
  const t = useT()
  return (
    <Suspense fallback={<p>{t('common.loading')}</p>}>
      <AccountPageChunk />
    </Suspense>
  )
}

function Admin() {
  const t = useT()
  const { session } = useSession()
  return (
    <RequireAuth>
      {session?.user?.role === 'admin' || session?.session?.adminPendingMfa === true ? (
        <Suspense
          fallback={
            <Card title={t('shell.admin')}>
              <p>{t('common.loading')}</p>
            </Card>
          }
        >
          <AdminPage />
        </Suspense>
      ) : (
        <Card title={t('shell.admin')}>
          <p role="alert" className="notice" data-kind="error">
            {t('shell.notAdmin')}
          </p>
        </Card>
      )}
    </RequireAuth>
  )
}

// The route table: neutral paths, matched after the language prefix is taken off (ADR 0027). No
// first segment here may be two lowercase letters (a test pins it): that shape is a language code.
const PAGES: Readonly<Record<string, () => ReactNode>> = {
  '/signin': () => <SignInPage />,
  '/signin/email': () => <ProviderEmailPage />,
  '/signup': () => <SignUpPage />,
  '/signup/confirm': () => <SignUpConfirmPage />,
  '/reset': () => <ResetPage />,
  '/email-change': () => <EmailChangePage />,
  '/email-revert': () => <EmailRevertPage />,
  '/unsubscribe': () => <UnsubscribePage />,
  // The provider's consent step for classbeat-player (ADR 0034); the server sends the browser here.
  '/player-consent': () => <PlayerConsentPage />,
  '/privacy': () => <PrivacyPage />,
  '/terms': () => <TermsPage />,
  '/help': () => <HelpPage />,
  '/examples': () => <ExamplesPage />,
  // One page per fixture entry; a slug that is not in the fixture is not a route.
  ...Object.fromEntries(EXAMPLES.map((example) => [`/examples/${example.slug}`, () => <ExamplePage example={example} />])),
  '/how-it-works': () => <HowItWorksPage />,
  '/features': () => <FeaturesPage />,
  '/pricing': () => <PricingPage />,
  // Draft legal pages (PB-4, #975): routed and prerendered, not published, not linked.
  '/terms-draft': () => <TermsDraftPage />,
  '/privacy-draft': () => <PrivacyDraftPage />,
  '/refunds': () => <RefundsDraftPage />,
  '/sub-processors': () => <SubProcessorsDraftPage />,
  '/classes': () => <ClassesPage />,
  '/classes/new': () => <NewClassPage />,
  '/classes/new/advanced': () => <Advanced />,
  '/music': () => <MusicPage />,
  '/account': () => <Account />,
  // Connections with the Spotify dialog open; Spotify's callback returns here (`?result=`).
  '/account/spotify': () => <Account />,
  // Connections with the classbeat-player dialog open; the player's link returns here (`?result=`).
  '/account/player': () => <Account />,
}

/** Every path pattern the app answers, for the guard that keeps two-letter first segments free. */
export const ROUTE_PATHS: readonly string[] = [
  '/',
  ...Object.keys(PAGES),
  '/jobs/:id',
  '/classes/:id',
  '/admin',
  '/admin/*',
]

function Routes() {
  const t = useT()
  const { lang, path, pathname } = useRouter()
  const { session } = useSession()
  // An unprefixed URL shows nothing but the loading state: `AccountLocale` redirects it once the
  // session has answered, and no page may mount (and, for a mail link, strip its token) before.
  if (lang === null && !isAdminPath(path) && !hasUnknownLanguage(pathname)) {
    return (
      <Card title={t('shell.requireAuth.loadingTitle')}>
        <p>{t('common.loading')}</p>
      </Card>
    )
  }
  const job = /^\/jobs\/([0-9a-fA-F-]{36})$/.exec(path)
  if (job?.[1] !== undefined) return <JobPage key={job[1]} jobId={job[1]} />
  const cls = /^\/classes\/([0-9a-fA-F-]{36})$/.exec(path)
  if (cls?.[1] !== undefined) return <ClassPage key={cls[1]} classId={cls[1]} />
  if (isAdminPath(path)) return <Admin />
  if (path === '/') return session?.authenticated === true ? <HomePage /> : <LandingPage />
  const page = Object.hasOwn(PAGES, path) ? PAGES[path] : undefined
  if (page !== undefined && lang !== null) return page()
  return (
    <section className="card">
      <h1 className="card__title">{t('shell.notFound')}</h1>
      <p>
        <Link to="/">{t('shell.backToStart')}</Link>
      </p>
    </section>
  )
}

function LoadingCard() {
  const t = useT()
  return (
    <Card title={t('shell.requireAuth.loadingTitle')}>
      <p>{t('common.loading')}</p>
    </Card>
  )
}

/** `initialLocation` is for the build-time prerender, which has no window; the browser reads its own. */
export function App({ initialLocation }: { initialLocation?: Location }) {
  return (
    <SessionProvider>
      <RouterProvider initialLocation={initialLocation}>
        <AccountLocale />
        <GuardProvider>
          <Banner />
          <AppShell>
            <Suspense fallback={<LoadingCard />}>
              <Routes />
            </Suspense>
          </AppShell>
          <CookieNotice />
        </GuardProvider>
      </RouterProvider>
    </SessionProvider>
  )
}
