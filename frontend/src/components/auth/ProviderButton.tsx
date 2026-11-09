import type { ButtonHTMLAttributes, CSSProperties, ReactNode } from 'react'
import { cn } from '../../lib/cn'

// Sign-in buttons in each provider's own look (ADR 0028 section 4): the provider's logo with its
// colours, its button colours, the same in both themes. Colours are tokens (tokens.css); the
// accessible name is the visible text, "Continue with <provider>".

const fill = (token: string): CSSProperties => ({ fill: `var(--cb-${token})` })

function Google() {
  return (
    <svg aria-hidden="true" viewBox="0 0 48 48" className="size-5 shrink-0">
      <path style={fill('google-red')} d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z" />
      <path style={fill('google-blue')} d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z" />
      <path style={fill('google-yellow')} d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z" />
      <path style={fill('google-green')} d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z" />
    </svg>
  )
}

function Microsoft() {
  return (
    <svg aria-hidden="true" viewBox="0 0 21 21" className="size-5 shrink-0">
      <rect x="1" y="1" width="9" height="9" style={fill('microsoft-red')} />
      <rect x="11" y="1" width="9" height="9" style={fill('microsoft-green')} />
      <rect x="1" y="11" width="9" height="9" style={fill('microsoft-blue')} />
      <rect x="11" y="11" width="9" height="9" style={fill('microsoft-yellow')} />
    </svg>
  )
}

function GitHub() {
  return (
    <svg aria-hidden="true" viewBox="0 0 16 16" className="size-5 shrink-0 fill-current">
      <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z" />
    </svg>
  )
}

const LOOK: Record<string, { mark: () => ReactNode; className: string }> = {
  google: { mark: Google, className: 'border-google-line bg-google-bg text-google-text' },
  microsoft: { mark: Microsoft, className: 'border-transparent bg-microsoft-bg text-microsoft-text' },
  github: { mark: GitHub, className: 'border-line bg-raised text-text' },
}

/** A "Continue with <provider>" button with the provider's logo and colours. */
export function ProviderButton({
  provider,
  className,
  children,
  ...props
}: { provider: string } & Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'type'>) {
  const look = Object.hasOwn(LOOK, provider) ? LOOK[provider] : undefined
  const Mark = look?.mark
  return (
    <button
      type="button"
      className={cn(
        'inline-flex min-h-11 w-full cursor-pointer items-center justify-center gap-3 rounded-full border px-5 text-[0.9375rem] font-medium transition-opacity duration-150 hover:opacity-90',
        'disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand',
        look?.className ?? 'border-line bg-raised text-text',
        className,
      )}
      {...props}
    >
      {Mark ? <Mark /> : null}
      {children}
    </button>
  )
}
