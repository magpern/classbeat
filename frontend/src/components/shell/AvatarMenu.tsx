import { LogOut, Settings, ShieldCheck } from 'lucide-react'
import { useRouter } from '../../router/Router'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '../ui/dropdown-menu'

function initial(email: string): string {
  const first = [...email.trim()][0]
  return first === undefined ? '?' : first.toUpperCase()
}

/** The avatar with Account, Admin (admins only) and Sign out. Named by the email of the user. */
export function AvatarMenu({
  email,
  isAdmin,
  busy,
  onSignOut,
  showName,
  side = 'top',
}: {
  email: string
  isAdmin: boolean
  busy: boolean
  onSignOut: () => void
  /** The sidebar shows the name beside the avatar; the other layouts only the avatar. */
  showName?: boolean
  /** Where the menu opens: up from the bottom of the sidebar, down from the phone header. */
  side?: 'top' | 'bottom'
}) {
  const { navigate } = useRouter()
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          aria-label={email}
          className="inline-flex min-h-11 min-w-11 cursor-pointer items-center gap-3 rounded-full border-0 bg-transparent p-0 text-text hover:bg-raised"
        >
          <span
            aria-hidden="true"
            className="inline-flex size-11 shrink-0 items-center justify-center rounded-full bg-brand font-display text-base font-bold text-on-brand"
          >
            {initial(email)}
          </span>
          {showName ? <span className="truncate pr-3 text-[0.9375rem]">{email}</span> : null}
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" side={side}>
        <DropdownMenuItem
          onSelect={() => {
            navigate('/account')
          }}
        >
          <Settings aria-hidden="true" size={18} strokeWidth={2} className="mr-2" />
          Account
        </DropdownMenuItem>
        {isAdmin ? (
          <DropdownMenuItem
            onSelect={() => {
              navigate('/admin')
            }}
          >
            <ShieldCheck aria-hidden="true" size={18} strokeWidth={2} className="mr-2" />
            Admin
          </DropdownMenuItem>
        ) : null}
        <DropdownMenuItem disabled={busy} onSelect={onSignOut}>
          <LogOut aria-hidden="true" size={18} strokeWidth={2} className="mr-2" />
          Sign out
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
