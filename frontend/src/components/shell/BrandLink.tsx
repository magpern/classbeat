import { useT } from '../../i18n/I18n'
import { Link } from '../../router/Router'
import { Logo } from '../Logo'

export function BrandLink({ compact }: { compact?: boolean }) {
  const t = useT()
  return (
    <Link to="/" className="inline-flex min-h-11 items-center gap-3 text-text no-underline" aria-label={t('shell.brandHome')}>
      <Logo />
      {compact ? null : <span className="font-display text-xl font-extrabold tracking-tight">classbeat</span>}
    </Link>
  )
}
