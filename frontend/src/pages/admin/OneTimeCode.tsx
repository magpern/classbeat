import { useState, type ReactNode } from 'react'
import { Button } from '../../components/ui/button'

export interface Shown {
  what: 'invite' | 'redeem code'
  code: string
}

/**
 * A new code, shown once (ADR 0011 section 6): the create response is the only place it exists.
 * It stays here with a copy button until the admin closes it or leaves the page, and is never kept
 * anywhere else (no storage, no URL). `children` is a note placed above the code.
 */
export function OneTimeCode({ shown, onDone, children }: { shown: Shown; onDone: () => void; children?: ReactNode }) {
  const [copied, setCopied] = useState<boolean | null>(null)
  const copy = () => {
    const clipboard = typeof navigator === 'undefined' ? undefined : navigator.clipboard
    if (clipboard === undefined) {
      setCopied(false)
      return
    }
    clipboard.writeText(shown.code).then(
      () => {
        setCopied(true)
      },
      () => {
        setCopied(false)
      },
    )
  }
  return (
    <section aria-label="New code" className="rounded-lg border border-brand bg-surface p-5" data-testid="one-time-code">
      <h2 className="m-0 mb-2 font-display text-xl font-bold">Your new {shown.what}</h2>
      <p className="m-0 mb-3 text-muted">Copy it now. It is shown only this once and cannot be shown again.</p>
      {children}
      <p className="m-0 mb-3 font-mono text-lg break-all select-all">{shown.code}</p>
      <div className="flex flex-wrap items-center gap-2">
        <Button onClick={copy}>Copy</Button>
        <Button variant="secondary" onClick={onDone}>
          Done
        </Button>
        {copied === true ? <span role="status">Copied.</span> : null}
        {copied === false ? <span role="status">Copy it by hand: the browser did not allow copying.</span> : null}
      </div>
    </section>
  )
}
