import { useT } from '../../i18n/I18n'
import { cn } from '../../lib/cn'
import { Link } from '../../router/Router'
import { NEW, TAB_ITEMS, isActive, type NavItem } from './navItems'

function Tab({ item, pathname }: { item: NavItem; pathname: string }) {
  const t = useT()
  const Icon = item.icon
  const active = isActive(item, pathname)
  const primary = item === NEW
  return (
    <Link
      to={item.to}
      aria-current={active ? 'page' : undefined}
      aria-label={primary ? t(item.label) : undefined}
      className={cn(
        'inline-flex min-h-11 min-w-11 items-center no-underline transition-colors duration-150',
        primary
          ? 'size-14 -mt-6 justify-center rounded-full border-4 border-solid border-bg bg-heat text-on-heat shadow-glow'
          : cn('flex-col justify-center gap-0.5 text-[0.6875rem]', active ? 'font-semibold text-brand' : 'text-muted'),
      )}
    >
      <Icon aria-hidden="true" size={primary ? 28 : 22} strokeWidth={2} />
      {primary ? null : <span>{t(item.label)}</span>}
    </Link>
  )
}

/** The phone's bottom tab bar; the + is the heat gradient (ADR 0013 Afterburn A2). */
export function TabBar({ pathname }: { pathname: string }) {
  const t = useT()
  return (
    <nav
      aria-label={t('shell.nav.main')}
      className="fixed inset-x-0 bottom-0 z-30 flex items-end justify-around border-t border-line bg-surface px-2 pb-2"
    >
      {TAB_ITEMS.map((item) => (
        <Tab key={item.to} item={item} pathname={pathname} />
      ))}
    </nav>
  )
}
