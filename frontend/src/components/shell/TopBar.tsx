import { Plus } from 'lucide-react'
import type { ReactNode } from 'react'
import { useT } from '../../i18n/I18n'
import { cn } from '../../lib/cn'
import { Link } from '../../router/Router'
import { BrandLink } from './BrandLink'
import { BAR_ITEMS, NEW, isActive } from './navItems'

/** The signed-in top bar from 640 px: logo, page pills, New class and the avatar (ADR 0013 Afterburn A2). */
export function TopBar({ pathname, menu }: { pathname: string; menu: ReactNode }) {
  const t = useT()
  return (
    <header className="sticky top-0 z-20 border-b border-line bg-bg">
      <div className="mx-auto flex max-w-5xl items-center gap-x-4 px-6 py-3">
        <BrandLink />
        <nav aria-label={t('shell.nav.main')} className="flex flex-1 flex-wrap gap-1.5">
          {BAR_ITEMS.map((item) => {
            const active = isActive(item, pathname)
            return (
              <Link
                key={item.to}
                to={item.to}
                aria-current={active ? 'page' : undefined}
                className={cn(
                  'inline-flex min-h-11 items-center rounded-full px-4 text-[0.9375rem] no-underline transition-colors duration-150',
                  active ? 'bg-raised font-semibold text-text' : 'font-medium text-muted hover:bg-raised hover:text-text',
                )}
              >
                {t(item.label)}
              </Link>
            )
          })}
        </nav>
        <Link
          to={NEW.to}
          className="inline-flex min-h-11 items-center gap-2 rounded-full bg-heat px-5 text-[0.9375rem] font-bold text-on-heat no-underline shadow-glow transition-[filter] duration-150 hover:brightness-110"
        >
          <Plus aria-hidden="true" size={18} strokeWidth={2.5} />
          {t(NEW.label)}
        </Link>
        {menu}
      </div>
    </header>
  )
}
