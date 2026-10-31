import { Home, Library, Music, Plus, User, type LucideIcon } from 'lucide-react'
import type { ReactNode } from 'react'
import { logout } from '../../api/auth'
import { useSession } from '../../auth/SessionContext'
import { useLayout, type Layout } from '../../hooks/useLayout'
import { cn } from '../../lib/cn'
import { Link, useRouter } from '../../router/Router'
import { ApiStatus } from '../ApiStatus'
import { useAction } from '../forms'
import { Logo } from '../Logo'
import { AvatarMenu } from './AvatarMenu'
import { ClassesThisMonth } from './ClassesThisMonth'

const JOB_PAGE = /^\/jobs\/[0-9a-fA-F-]{36}$/

/** New class and Designing hide the navigation so nothing distracts mid-task (ADR 0013 section 4). */
export function isFocusRoute(pathname: string): boolean {
  const path = pathname.replace(/\/+$/, '') || '/'
  return path === '/classes/new' || JOB_PAGE.test(path)
}

interface Item {
  to: string
  label: string
  icon: LucideIcon
  primary?: boolean
}

const HOME: Item = { to: '/', label: 'Home', icon: Home }
const CLASSES: Item = { to: '/classes', label: 'My classes', icon: Library }
const NEW: Item = { to: '/classes/new', label: 'New class', icon: Plus, primary: true }
const MUSIC: Item = { to: '/music', label: 'Music', icon: Music }
const ACCOUNT: Item = { to: '/account', label: 'Account', icon: User }

function itemsFor(layout: Layout): Item[] {
  // The DOM order is the visual order: the pill first in the sidebar and rail, in the middle of the tab bar.
  return layout === 'phone' ? [HOME, CLASSES, NEW, MUSIC, ACCOUNT] : [NEW, HOME, CLASSES, MUSIC, ACCOUNT]
}

function isActive(item: Item, pathname: string): boolean {
  const path = pathname.replace(/\/+$/, '') || '/'
  if (item.to === '/') return path === '/'
  if (item.to === '/classes') return path === '/classes' || (path.startsWith('/classes/') && path !== '/classes/new')
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
  const Icon = item.icon
  const active = isActive(item, pathname)
  const iconOnly = layout === 'rail' || (item.primary === true && layout === 'phone')
  return (
    <Link
      to={item.to}
      aria-current={active ? 'page' : undefined}
      aria-label={iconOnly ? item.label : undefined}
      className={cn(
        'inline-flex min-h-11 min-w-11 items-center no-underline transition-colors duration-150',
        lookOf(item, layout, active),
      )}
    >
      <Icon aria-hidden="true" size={item.primary === true && layout !== 'sidebar' ? 28 : 22} strokeWidth={2} />
      {iconOnly ? null : <span>{item.label}</span>}
    </Link>
  )
}

function PrimaryNav({ layout, pathname }: { layout: Layout; pathname: string }) {
  return (
    <nav
      aria-label="Main"
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
  return (
    <Link to="/" className="inline-flex min-h-11 items-center gap-3 text-text no-underline" aria-label="classbeat home">
      <Logo />
      {compact ? null : <span className="font-display text-xl font-extrabold tracking-tight">classbeat</span>}
    </Link>
  )
}

function SignedOutHeader({ ready }: { ready: boolean }) {
  return (
    <header className="app-header">
      <div className="app-header__inner">
        <Link to="/" className="brand">
          <Logo />
          <span className="brand__name">classbeat</span>
        </Link>
        {ready ? (
          <nav className="nav" aria-label="Account">
            <Link to="/signin">Sign in</Link>
            <Link to="/signup">Sign up</Link>
          </nav>
        ) : null}
        <ApiStatus />
      </div>
    </header>
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
        </div>
      </main>
      {bottom}
    </div>
  )
}

/** The page frame: signed out, a plain header; signed in, the navigation for the width (ADR 0013 section 4). */
export function AppShell({ children }: { children: ReactNode }) {
  const { status, session, refresh } = useSession()
  const { pathname, navigate } = useRouter()
  const layout = useLayout()
  const action = useAction()

  if (status !== 'ready' || session === null || !session.authenticated) {
    return (
      <Frame
        top={<SignedOutHeader ready={status === 'ready' && session !== null} />}
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
  const alert = action.error ? (
    <p role="alert" className="m-0 mb-4 rounded-md bg-bad-soft px-4 py-3 text-bad">
      {action.error}
    </p>
  ) : null

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
              {pathname.startsWith('/jobs/') ? 'Back to home' : 'Close'}
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

  const menu = (showName: boolean) => (
    <AvatarMenu
      email={email}
      isAdmin={session.user?.role === 'admin'}
      busy={action.busy}
      onSignOut={signOut}
      showName={showName}
    />
  )

  if (layout === 'phone') {
    return (
      <Frame
        top={
          <header className="sticky top-0 z-20 flex items-center justify-between border-b border-line bg-surface px-4 py-2">
            <BrandLink />
            {menu(false)}
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
            {sidebar ? <ClassesThisMonth /> : null}
            {sidebar ? <ApiStatus /> : null}
            {menu(sidebar)}
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
