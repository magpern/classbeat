function initial(text: string): string {
  const first = [...text.trim()][0]
  return first === undefined ? '?' : first.toUpperCase()
}

/** The name shown for an account: the part of the address before the `@`, or the address itself. */
export function accountName(email: string): string {
  const at = email.indexOf('@')
  return at > 0 ? email.slice(0, at) : email
}

/**
 * The Account page's identity strip (ADR 0028): an avatar, the name as the page's one `h1` and the
 * address beneath it. Changing the address lives in the Sign-in and security tab.
 */
export function AccountHeader({ email }: { email: string }) {
  const name = accountName(email)
  return (
    <header className="flex items-center gap-4 rounded-lg border border-line bg-surface p-5">
      <span
        aria-hidden="true"
        className="inline-flex size-16 shrink-0 items-center justify-center rounded-full bg-brand font-display text-2xl font-bold text-on-brand"
      >
        {initial(name)}
      </span>
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <h1 className="m-0 font-display text-2xl font-bold break-words">{name}</h1>
        {name !== email ? (
          <p className="m-0 break-words text-muted" data-testid="account-email">
            {email}
          </p>
        ) : null}
      </div>
    </header>
  )
}
