import { Home, Library, Music, Plus, User, type LucideIcon } from 'lucide-react'
import type { CatalogueKey } from '../../i18n/en'

/** New class (the start screen and the advanced form) and Designing hide the navigation so nothing distracts mid-task (ADR 0013 section 4). */
const JOB_PAGE = /^\/jobs\/[0-9a-fA-F-]{36}$/

export function isFocusRoute(pathname: string): boolean {
  const path = pathname.replace(/\/+$/, '') || '/'
  return path === '/classes/new' || path === '/classes/new/advanced' || JOB_PAGE.test(path)
}

export interface NavItem {
  to: string
  label: CatalogueKey
  icon: LucideIcon
}

export const HOME: NavItem = { to: '/', label: 'shell.nav.home', icon: Home }
export const CLASSES: NavItem = { to: '/classes', label: 'shell.nav.classes', icon: Library }
export const NEW: NavItem = { to: '/classes/new', label: 'shell.nav.new', icon: Plus }
export const MUSIC: NavItem = { to: '/music', label: 'shell.nav.music', icon: Music }
export const ACCOUNT: NavItem = { to: '/account', label: 'shell.nav.account', icon: User }

/** The top bar's page links; Home is the logo, Account the avatar menu (ADR 0013 Afterburn A2). */
export const BAR_ITEMS: readonly NavItem[] = [CLASSES, MUSIC]
/** The phone's tab bar, the + in the middle. */
export const TAB_ITEMS: readonly NavItem[] = [HOME, CLASSES, NEW, MUSIC, ACCOUNT]

export function isActive(item: NavItem, pathname: string): boolean {
  const path = pathname.replace(/\/+$/, '') || '/'
  if (item.to === '/') return path === '/'
  if (item.to === '/classes') return path === '/classes' || (path.startsWith('/classes/') && path !== '/classes/new' && !path.startsWith('/classes/new/'))
  return path === item.to || path.startsWith(`${item.to}/`)
}
