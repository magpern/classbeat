import type { ReactNode } from 'react'
import { useT } from '../../i18n/I18n'
import { PageHeading } from '../ui/page-heading'
import type { AccountStats } from './useAccountStats'

function initial(text: string): string {
  const first = [...text.trim()][0]
  return first === undefined ? '?' : first.toUpperCase()
}

/** The name shown for an account: the part of the address before the `@`, or the address itself. */
export function accountName(email: string): string {
  const at = email.indexOf('@')
  return at > 0 ? email.slice(0, at) : email
}

function Tile({ label, children, testId }: { label: string; children: ReactNode; testId: string }) {
  return (
    <div className="flex min-w-0 flex-col gap-1.5 rounded-2xl bg-raised px-4 py-3.5" data-testid={testId}>
      <span className="text-xs font-semibold tracking-[0.14em] text-muted uppercase">{label}</span>
      <span className="text-[1.0625rem] font-bold break-words tabular-nums">{children}</span>
    </div>
  )
}

/**
 * The Account page's profile card (ADR 0013 Afterburn A3): a heat line on top, the avatar in a heat
 * ring, the name as the page's one `h1`, the address, and three tiles (plan, classes this month,
 * required connections linked). A tile whose figure is not known (loading, or it could not be
 * read) is left out. Changing the address lives in the Sign-in and security tab.
 */
export function AccountHeader({ email, stats }: { email: string; stats?: AccountStats }) {
  const t = useT()
  const name = accountName(email)
  const tiles = stats ?? { planName: null, classes: null, linked: null }
  return (
    <header className="relative flex flex-wrap items-center gap-6 overflow-hidden rounded-xl border border-line bg-surface p-6 shadow-card sm:p-8">
      <div aria-hidden="true" className="absolute inset-x-0 top-0 h-1 bg-heat" />
      <span aria-hidden="true" className="inline-flex size-20 shrink-0 rounded-full bg-heat p-[3px] shadow-glow">
        <span className="inline-flex flex-1 items-center justify-center rounded-full bg-surface font-display text-3xl font-extrabold">
          {initial(name)}
        </span>
      </span>
      <div className="flex min-w-0 flex-[1_1_16rem] flex-col gap-1.5">
        <PageHeading
          eyebrow={t('account.eyebrow')}
          title={name}
          titleClassName="text-4xl sm:text-4xl leading-tight break-words"
        />
        <p className="m-0 break-words text-muted" data-testid="account-email">
          {email}
        </p>
      </div>
      {tiles.planName !== null || tiles.classes !== null || tiles.linked !== null ? (
        <div className="grid min-w-0 flex-[1_1_24rem] grid-cols-1 gap-2.5 sm:grid-cols-3">
          {tiles.planName !== null ? (
            <Tile label={t('account.stat.plan')} testId="account-stat-plan">
              {tiles.planName}
            </Tile>
          ) : null}
          {tiles.classes !== null ? (
            <Tile label={t('account.stat.classes')} testId="account-stat-classes">
              {tiles.classes}
            </Tile>
          ) : null}
          {tiles.linked !== null ? (
            <Tile label={t('account.stat.linked')} testId="account-stat-linked">
              {t('account.stat.linkedValue', { count: tiles.linked })}
            </Tile>
          ) : null}
        </div>
      ) : null}
    </header>
  )
}
