import { LegalFooter } from '../../pages/legal/LegalFooter'
import { Home, Library, Music, Plus, User, type LucideIcon } from 'lucide-react'
import type { ReactNode } from 'react'
import { logout } from '../../api/auth'
import { useSession } from '../../auth/SessionContext'
import { useT } from '../../i18n/I18n'
import type { CatalogueKey } from '../../i18n/en'
import { useLayout, type Layout } from '../../hooks/useLayout'
import { cn } from '../../lib/cn'
import { Link, useRouter } from '../../router/Router'
import { useAction } from '../forms'
import { LOCALES } from '../../i18n/locales'
import { LanguagePicker } from '../LanguagePicker'
import { Logo } from '../Logo'
import { AvatarMenu } from './AvatarMenu'
import { ClassesThisMonth } from './ClassesThisMonth'
import { PhoneSignInNotice } from './PhoneSignInNotice'

const JOB_PAGE = /^\/jobs\/[0-9a-fA-F-]{36}$/

/** New class (the start screen and the advanced form) and Designing hide the navigation so nothing distracts mid-task (ADR 0013 section 4). */
export function isFocusRoute(pathname: string): boolean {
  const path = pathname.replace(/\/+$/, '') || '/'
  return path === '/classes/new' || path === '/classes/new/advanced' || JOB_PAGE.test(path)
}

interface Item {
  to: string
  label: CatalogueKey
  icon: LucideIcon
  primary?: boolean
}

const HOME: Item = { to: '/', label: 'shell.nav.home', icon: Home }
const CLASSES: Item = { to: '/classes', label: 'shell.nav.classes', icon: Library }
const NEW: Item = { to: '/classes/new', label: 'shell.nav.new', icon: Plus, primary: true }
const MUSIC: Item = { to: '/music', label: 'shell.nav.music', icon: Music }
const ACCOUNT: Item = { to: '/account', label: 'shell.nav.account', icon: User }

function itemsFor(layout: Layout): Item[] {
  // The DOM order is the visual order: the pill first in the sidebar and rail, in the middle of the tab bar.
  return layout === 'phone' ? [HOME, CLASSES, NEW, MUSIC, ACCOUNT] : [NEW, HOME, CLASSES, MUSIC, ACCOUNT]
}

function isActive(item: Item, pathname: string): boolean {
  const path = pathname.replace(/\/+$/, '') || '/'
  if (item.to === '/') return path === '/'
  if (item.to === '/classes') return path === '/classes' || (path.startsWith('/classes/') && path !== '/classes/new' && !path.startsWith('/classes/new/'))
  return path === item.to || path.startsWith(`${item.to}/`)
}

function lookOf(item: Item, layout: Layout, active: boolean): string {
  if (item.primary) {
    if (layout === 'phone') return 'size-14 -mt-6 justify-center rounded-full border-4 border-bg bg-brand text-on-brand'
    if (layout === 'rail') return 'size-12 justify-center self-center rounded-full bg-brand text-on-brand'
    return 'justify-center gap-2 rounded-full bg-brand px-5 font-medium text-on-brand'
  }
  if (layout === 'phone') return cn('flex-col justify-center gap-0.5 text-[0.6875rem]', active ? 'text-brand' : 'text-muted')
  return cn(
    'gap-3 rounded-md px-3 font-medium',
    layout === 'rail' && 'justify-center',
    active ? 'bg-raised text-text' : 'text-muted hover:bg-raised hover:text-text',
  )
}

function NavLink({ item, layout, pathname }: { item: Item; layout: Layout; pathname: string }) {
  const t = useT()
  const Icon = item.icon
  const active = isActive(item, pathname)
  const iconOnly = layout === 'rail' || (item.primary === true && layout === 'phone')
  return (
    <Link
      to={item.to}
      aria-current={active ? 'page' : undefined}
      aria-label={iconOnly ? t(item.label) : undefined}
      className={cn(
        'inline-flex min-h-11 min-w-11 items-center no-underline transition-colors duration-150',
        lookOf(item, layout, active),
      )}
    >
      <Icon aria-hidden="true" size={item.primary === true && layout !== 'sidebar' ? 28 : 22} strokeWidth={2} />
      {iconOnly ? null : <span>{t(item.label)}</span>}
    </Link>
  )
}

function PrimaryNav({ layout, pathname }: { layout: Layout; pathname: string }) {
  const t = useT()
  return (
    <nav
      aria-label={t('shell.nav.main')}
      className={
        layout === 'phone'
          ? 'fixed inset-x-0 bottom-0 z-30 flex items-end justify-around border-t border-line bg-surface px-2 pb-2'
          : 'flex flex-col gap-1'
      }
    >
      {itemsFor(layout).map((item) => (
        <NavLink key={item.to} item={item} layout={layout} pathname={pathname} />
      ))}
    </nav>
  )
}

function BrandLink({ compact }: { compact?: boolean }) {
  const t = useT()
  return (
    <Link to="/" className="inline-flex min-h-11 items-center gap-3 text-text no-underline" aria-label={t('shell.brandHome')}>
      <Logo />
      {compact ? null : <span className="font-display text-xl font-extrabold tracking-tight">classbeat</span>}
    </Link>
  )
}

function SignedOutHeader({ ready, offered }: { ready: boolean; offered: readonly string[] }) {
  const t = useT()
  return (
    <header className="border-b border-line bg-surface">
      <div className="mx-auto flex max-w-5xl flex-wrap items-center gap-x-4 gap-y-2 px-4 py-2">
        <BrandLink />
        {ready ? (
          <nav aria-label={t('shell.nav.account')} className="ml-auto flex flex-wrap items-center justify-end gap-2">
            <LanguagePicker offered={offered} />
            <Link
              to="/signin"
              className="inline-flex min-h-11 items-center rounded-full px-4 font-medium text-text no-underline hover:bg-raised"
            >
              {t('auth.signIn')}
            </Link>
            <Link
              to="/signup"
              className="inline-flex min-h-11 items-center rounded-full bg-brand px-5 font-medium text-on-brand no-underline hover:opacity-90"
            >
              {t('shell.signUp')}
            </Link>
          </nav>
        ) : (
          <NavPlaceholder />
        )}
      </div>
    </header>
  )
}

/**
 * The signed-out nav's room while the session loads, which is also how the prerender writes it
 * (ADR 0030): the same row of controls at the same height, with no text and no link, so the page
 * below does not move when the real nav arrives (the takeover gate). A signed-in person never sees
 * sign-in links flash. The widths are those of the controls in English.
 */
function NavPlaceholder() {
  return (
    <div aria-hidden="true" data-testid="nav-placeholder" className="ml-auto flex flex-wrap items-center justify-end gap-2">
      {LOCALES.length > 1 ? <span className="inline-block h-11 w-[6.5rem] rounded-md" /> : null}
      <span className="inline-block h-11 w-[5.25rem] rounded-full" />
      <span className="inline-block h-11 w-[6.25rem] rounded-full" />
    </div>
  )
}

interface FrameProps {
  rootClass?: string
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
function Frame({ rootClass, top, mainClass, alert, bottom, children }: FrameProps) {
  return (
    <div className={rootClass}>
      {top}
      <main className={mainClass}>
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

  const menu = (showName: boolean, side: 'top' | 'bottom') => (
    <AvatarMenu
      email={email}
      isAdmin={session.user?.role === 'admin' || session.session?.adminPendingMfa === true}
      busy={action.busy}
      onSignOut={signOut}
      showName={showName}
      side={side}
    />
  )

  if (layout === 'phone') {
    return (
      <Frame
        top={
          <header className="sticky top-0 z-20 flex items-center justify-between border-b border-line bg-surface px-4 py-2">
            <BrandLink />
            {menu(false, 'bottom')}
          </header>
        }
        mainClass="px-4 pt-6 pb-28"
        alert={alert}
        bottom={<PrimaryNav layout={layout} pathname={pathname} />}
      >
        {children}
      </Frame>
    )
  }

  const sidebar = layout === 'sidebar'
  return (
    <Frame
      rootClass="flex min-h-screen"
      top={
        <aside
          className={cn(
            'sticky top-0 flex h-screen shrink-0 flex-col gap-4 border-r border-line bg-surface p-3',
            sidebar ? 'w-64' : 'w-20 items-center',
          )}
        >
          <BrandLink compact={!sidebar} />
          <PrimaryNav layout={layout} pathname={pathname} />
          <div className="mt-auto flex flex-col gap-3">
            <ClassesThisMonth compact={!sidebar} />
            {menu(sidebar, 'top')}
          </div>
        </aside>
      }
      mainClass="min-w-0 flex-1 px-6 py-8"
      alert={alert}
    >
      {children}
    </Frame>
  )
}
