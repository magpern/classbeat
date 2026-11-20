import { LogOut, Settings, ShieldCheck } from 'lucide-react'
import { useT } from '../../i18n/I18n'
import { useRouter } from '../../router/Router'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '../ui/dropdown-menu'

function initial(email: string): string {
  const first = [...email.trim()][0]
  return first === undefined ? '?' : first.toUpperCase()
}

/** The avatar in a heat ring, opening Account, Admin (admins only) and Sign out. Named by the email of the user. */
export function AvatarMenu({
  email,
  isAdmin,
  busy,
  onSignOut,
}: {
  email: string
  isAdmin: boolean
  busy: boolean
  onSignOut: () => void
}) {
  const t = useT()
  const { navigate } = useRouter()
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          aria-label={email}
          // The heat ring (ADR 0013 Afterburn A1): the gradient shows through the 2 px padding.
          className="inline-flex size-11 shrink-0 cursor-pointer items-center justify-center rounded-full border-0 bg-heat p-0.5 text-text"
        >
          <span
            aria-hidden="true"
            className="inline-flex size-full items-center justify-center rounded-full bg-bg font-display text-base font-bold"
          >
            {initial(email)}
          </span>
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" side="bottom">
        <DropdownMenuItem
          onSelect={() => {
            navigate('/account')
          }}
        >
          <Settings aria-hidden="true" size={18} strokeWidth={2} className="mr-2" />
          {t('shell.nav.account')}
        </DropdownMenuItem>
        {isAdmin ? (
          <DropdownMenuItem
            onSelect={() => {
              navigate('/admin')
            }}
          >
            <ShieldCheck aria-hidden="true" size={18} strokeWidth={2} className="mr-2" />
            {t('shell.admin')}
          </DropdownMenuItem>
        ) : null}
        <DropdownMenuItem disabled={busy} onSelect={onSignOut}>
          <LogOut aria-hidden="true" size={18} strokeWidth={2} className="mr-2" />
          {t('shell.menu.signOut')}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
