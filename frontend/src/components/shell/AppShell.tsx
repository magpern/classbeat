import { LegalFooter } from '../../pages/legal/LegalFooter'
import { useRef, type ReactNode } from 'react'
import { logout } from '../../api/auth'
import { useSession } from '../../auth/SessionContext'
import { useT } from '../../i18n/I18n'
import { useLayout } from '../../hooks/useLayout'
import { useSpotlight } from '../../hooks/useSpotlight'
import { forgetExample } from '../../lib/exampleHandoff'
import { Link, useRouter } from '../../router/Router'
import { useAction } from '../forms'
import { AvatarMenu } from './AvatarMenu'
import { PhoneSignInNotice } from './PhoneSignInNotice'
import { BrandLink } from './BrandLink'
import { SignedOutHeader } from './SignedOutHeader'
import { TabBar } from './TabBar'
import { TopBar } from './TopBar'
import { isFocusRoute } from './navItems'

interface FrameProps {
  top: ReactNode
  mainClass: string
  alert: ReactNode
  bottom?: ReactNode
  children: ReactNode
}

/**
 * Every layout renders through this one structure (root, top, main, bottom), so the page in
 * main keeps its state when the session arrives or the width changes: a mail-link page reads and
 * strips its token once and must not be mounted again.
 */
function Frame({ top, mainClass, alert, bottom, children }: FrameProps) {
  const t = useT()
  const root = useRef<HTMLDivElement>(null)
  useSpotlight(root) // the pointer's light on every `.glow-card` (motion.css)
  return (
    <div ref={root}>
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-50 focus:rounded-full focus:bg-heat focus:px-5 focus:py-3 focus:font-medium focus:text-on-heat focus:no-underline"
      >
        {t('shell.skip')}
      </a>
      {top}
      <main id="main-content" tabIndex={-1} className={`${mainClass} focus:outline-none`}>
        <div className="mx-auto max-w-5xl">
          {alert}
          {children}
          <LegalFooter />
        </div>
      </main>
      {bottom}
    </div>
  )
}

/** The page frame: signed out, a plain header; signed in, the navigation for the width (ADR 0013 section 4). */
export function AppShell({ children }: { children: ReactNode }) {
  const { status, session, refresh } = useSession()
  const { path: pathname, navigate } = useRouter()
  const layout = useLayout()
  const t = useT()
  const action = useAction()

  if (status !== 'ready' || session === null || !session.authenticated) {
    return (
      <Frame
        top={
          <SignedOutHeader
            ready={status === 'ready' && session !== null}
            offered={session?.locales ?? []}
          />
        }
        mainClass="app-main"
        alert={null}
      >
        {children}
      </Frame>
    )
  }

  const email = session.user?.email ?? ''
  const signOut = () => {
    void action.run(async () => {
      await logout()
      // A kept example slug belongs to the browser, not the account: the next person must not inherit it.
      forgetExample()
      await refresh()
      navigate('/signin')
    })
  }
  const alert = (
    <>
      <PhoneSignInNotice busy={action.busy} onSignOut={signOut} />
      {action.error ? (
        <p role="alert" className="m-0 mb-4 rounded-md bg-bad-soft px-4 py-3 text-bad">
          {action.error}
        </p>
      ) : null}
    </>
  )

  if (isFocusRoute(pathname)) {
    return (
      <Frame
        top={
          <header className="flex items-center justify-between border-b border-line bg-surface px-4 py-2">
            <BrandLink />
            <Link
              to="/"
              className="inline-flex min-h-11 items-center rounded-full bg-raised px-5 font-medium text-text no-underline hover:bg-line"
            >
              {pathname.startsWith('/jobs/') ? t('shell.backHome') : t('shell.close')}
            </Link>
          </header>
        }
        mainClass="app-main"
        alert={alert}
      >
        {children}
      </Frame>
    )
  }

  const menu = (
    <AvatarMenu
      email={email}
      isAdmin={session.user?.role === 'admin' || session.session?.adminPendingMfa === true}
      busy={action.busy}
      onSignOut={signOut}
    />
  )

  if (layout === 'phone') {
    return (
      <Frame
        top={
          <header className="sticky top-0 z-20 flex items-center justify-between border-b border-line bg-bg px-4 py-2">
            <BrandLink />
            {menu}
          </header>
        }
        mainClass="px-4 pt-6 pb-28"
        alert={alert}
        bottom={<TabBar pathname={pathname} />}
      >
        {children}
      </Frame>
    )
  }

  return (
    <Frame top={<TopBar pathname={pathname} menu={menu} />} mainClass="min-w-0 px-6 py-8" alert={alert}>
      {children}
    </Frame>
  )
}
