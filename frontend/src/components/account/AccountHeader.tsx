import { Fragment, type ReactNode } from 'react'
import { cn } from '../../lib/cn'
import { useT } from '../../i18n/I18n'
import { PageHeading } from '../ui/page-heading'
import type { AccountStats } from './useAccountStats'

function initial(text: string): string {
  const first = [...text.trim()][0]
  return first === undefined ? '?' : first.toUpperCase()
}

/** Where a long address may wrap: before an `@` and after a `.`, `-` or `_`. */
const BREAK_POINTS = /(?=@)|(?<=[.\-_])/

/** An address longer than this is set in a smaller title, so it wraps less. */
const LONG_ADDRESS = 24

/**
 * The address with a `<wbr>` at each place it may wrap, so it breaks at `@`, `.`, `-` or `_` and
 * not inside a word. The text is unchanged (copying it gives the address back).
 */
function Address({ email }: { email: string }) {
  const parts = email.split(BREAK_POINTS)
  return (
    <span data-testid="account-email" title={email}>
      {parts.map((part, i) => (
        <Fragment key={i}>
          {i > 0 ? <wbr className="hidden sm:inline" /> : null}
          {part}
        </Fragment>
      ))}
    </span>
  )
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
 * ring, the address as the page's one `h1` (the sign-up asks for an email only, so there is no
 * other name), and three tiles (plan, classes this month,
 * required connections linked). A tile whose figure is not known (loading, or it could not be
 * read) is left out. Changing the address lives in the Sign-in and security tab.
 */
export function AccountHeader({ email, stats }: { email: string; stats?: AccountStats }) {
  const t = useT()
  const tiles = stats ?? { planName: null, classes: null, linked: null }
  return (
    <header className="relative flex flex-wrap items-center gap-4 overflow-hidden rounded-xl border border-line bg-surface p-4 shadow-card sm:gap-6 sm:p-8">
      <div aria-hidden="true" className="absolute inset-x-0 top-0 h-1 bg-heat" />
      {/* Phone: the avatar and the eyebrow share a row and the address is one line under them
          (cut with an ellipsis when it does not fit). From `sm`: the avatar beside both, and a
          long address wraps at its break points. */}
      <div className="grid min-w-0 flex-[1_1_16rem] sm:flex-[1_1_22.5rem] grid-cols-[auto_minmax(0,1fr)] items-center gap-x-3 gap-y-2 sm:gap-x-6 sm:gap-y-1">
        <span
          aria-hidden="true"
          className="inline-flex size-10 shrink-0 rounded-full bg-heat p-[2px] shadow-glow sm:row-span-2 sm:size-20 sm:p-[3px]"
        >
          <span className="inline-flex flex-1 items-center justify-center rounded-full bg-surface font-display text-lg font-extrabold sm:text-3xl">
            {initial(email)}
          </span>
        </span>
        <p className="m-0 min-w-0 text-xs font-semibold tracking-[0.14em] text-muted uppercase">{t('account.eyebrow')}</p>
        <PageHeading
          title={<Address email={email} />}
          titleClassName={cn(
            'col-span-2 hyphens-none sm:col-span-1',
            'truncate text-sm leading-snug font-stretch-[90%] sm:overflow-visible sm:text-clip sm:whitespace-normal sm:font-stretch-(--cb-font-stretch-display)',
            email.length > LONG_ADDRESS ? 'sm:text-2xl' : 'sm:text-3xl',
          )}
        />
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
