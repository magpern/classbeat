import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { adminWhoami, getMfaStatus } from '../../api/auth'
import { ApiError } from '../../api/client'
import { MfaPrompt, TotpEnroll } from '../../components/account/TwoFactor'
import { Card, Notice } from '../../components/forms'
import { cn } from '../../lib/cn'
import { Link, useRouter } from '../../router/Router'
import { StepUpProvider } from './StepUp'

type Gate =
  | { state: 'loading' }
  | { state: 'ok'; email: string }
  | { state: 'forbidden' }
  | { state: 'mfa'; enrolled: boolean }
  | { state: 'unavailable' }
  | { state: 'error' }

/** The server's 403 for a non-admin, as a plain page with no admin navigation. */
export function AdminForbidden() {
  return (
    <Card title="Admin">
      <Notice kind="error">Your account is not an admin account.</Notice>
    </Card>
  )
}

const SECTIONS = [
  { to: '/admin/users', label: 'Users' },
  { to: '/admin/codes', label: 'Codes' },
  { to: '/admin/usage', label: 'Usage' },
  { to: '/admin/jobs', label: 'Jobs' },
  { to: '/admin/audit', label: 'Audit log' },
  { to: '/admin/settings', label: 'Settings' },
] as const

function AdminNav() {
  const { pathname } = useRouter()
  return (
    <nav aria-label="Admin" className="mb-6 flex gap-1 border-b border-line">
      {SECTIONS.map((section) => {
        const active =
          pathname.startsWith(section.to) || (section.to === '/admin/users' && /^\/admin\/?$/.test(pathname))
        return (
          <Link
            key={section.to}
            to={section.to}
            aria-current={active ? 'page' : undefined}
            className={cn(
              'inline-flex min-h-11 items-center border-b-2 px-4 font-medium no-underline',
              active ? 'border-brand text-text' : 'border-transparent text-muted hover:text-text',
            )}
          >
            {section.label}
          </Link>
        )
      })}
    </nav>
  )
}

/**
 * The admin area's frame (ADR 0011 section 6, ADR 0003 section 7): `GET /api/admin/whoami`
 * first. A non-admin gets the plain 403 page; an admin without TOTP in the last 12 hours gets the
 * code prompt (or enrolment, the first time) and nothing else. The navigation and the pages
 * render only after the server said yes, inside the step-up provider every write goes through.
 */
export function AdminLayout({ children }: { children: ReactNode }) {
  const [gate, setGate] = useState<Gate>({ state: 'loading' })
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    const load = async (): Promise<Gate> => {
      try {
        return { state: 'ok', email: await adminWhoami(controller.signal) }
      } catch (error) {
        if (error instanceof ApiError && error.status === 403) {
          if (error.reason === 'mfa_required') {
            const status = await getMfaStatus(controller.signal)
            return { state: 'mfa', enrolled: status.totpEnabled }
          }
          return { state: 'forbidden' }
        }
        if (error instanceof ApiError && error.status === 503) return { state: 'unavailable' }
        return { state: 'error' }
      }
    }
    load().then(
      (next) => {
        if (!controller.signal.aborted) setGate(next)
      },
      () => {
        if (!controller.signal.aborted) setGate({ state: 'error' })
      },
    )
    return () => {
      controller.abort()
    }
  }, [attempt])

  const recheck = useCallback(() => {
    setAttempt((n) => n + 1)
  }, [])

  switch (gate.state) {
    case 'loading':
      return (
        <Card title="Admin">
          <p>Loading</p>
        </Card>
      )
    case 'forbidden':
      return <AdminForbidden />
    case 'unavailable':
      return (
        <Card title="Admin">
          <Notice kind="error">The admin area is not available right now.</Notice>
        </Card>
      )
    case 'error':
      return (
        <Card title="Admin">
          <Notice kind="error">Something went wrong. Try again in a moment.</Notice>
        </Card>
      )
    case 'mfa':
      return gate.enrolled ? (
        <Card title="Two-factor check">
          <p>Admins confirm a code from their authenticator app every 12 hours.</p>
          <MfaPrompt onVerified={recheck} />
        </Card>
      ) : (
        <Card title="Set up two-factor">
          <p>Admin accounts need an authenticator app. Set it up now to continue.</p>
          <TotpEnroll onEnrolled={recheck} />
        </Card>
      )
    case 'ok':
      return (
        <StepUpProvider onMfaRequired={recheck}>
          <div className="text-text">
            <header className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
              <h1 className="m-0 font-display text-2xl font-bold">Admin</h1>
              <p className="m-0 text-[0.9375rem] text-muted">Signed in as admin: {gate.email}</p>
            </header>
            <AdminNav />
            {children}
          </div>
        </StepUpProvider>
      )
  }
}
