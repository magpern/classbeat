import { useCallback, useEffect, useState } from 'react'
import { adminWhoami, getMfaStatus } from '../api/auth'
import { ApiError } from '../api/client'
import { Card, Notice } from '../components/forms'
import { RequireAuth } from '../components/RequireAuth'
import { MfaPrompt, TotpEnroll } from '../components/account/TwoFactor'

type View =
  | { state: 'loading' }
  | { state: 'ok'; email: string }
  | { state: 'forbidden' }
  | { state: 'mfa'; enrolled: boolean }
  | { state: 'error' }

export function AdminPage() {
  return (
    <RequireAuth>
      <Admin />
    </RequireAuth>
  )
}

/**
 * M1's admin area is only the MFA gate (ADR 0003 section 7): an admin sees nothing until the
 * session has passed TOTP, and enrols right here the first time.
 */
function Admin() {
  const [view, setView] = useState<View>({ state: 'loading' })
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    const load = async (): Promise<View> => {
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
        return { state: 'error' }
      }
    }
    load().then(
      (next) => {
        if (!controller.signal.aborted) setView(next)
      },
      () => {
        if (!controller.signal.aborted) setView({ state: 'error' })
      },
    )
    return () => {
      controller.abort()
    }
  }, [attempt])

  const reload = useCallback(() => {
    setAttempt((n) => n + 1)
  }, [])

  switch (view.state) {
    case 'loading':
      return (
        <Card title="Admin">
          <p>Loading</p>
        </Card>
      )
    case 'forbidden':
      return (
        <Card title="Admin">
          <Notice kind="error">Your account is not an admin account.</Notice>
        </Card>
      )
    case 'error':
      return (
        <Card title="Admin">
          <Notice kind="error">Something went wrong. Try again in a moment.</Notice>
        </Card>
      )
    case 'ok':
      return (
        <Card title="Admin">
          <Notice kind="success">Signed in as admin: {view.email}. The admin tools come later.</Notice>
        </Card>
      )
    case 'mfa':
      return view.enrolled ? (
        <Card title="Two-factor check">
          <p>Admins confirm a code from their authenticator app after signing in.</p>
          <MfaPrompt onVerified={reload} />
        </Card>
      ) : (
        <Card title="Set up two-factor">
          <p>Admin accounts need an authenticator app. Set it up now to continue.</p>
          <TotpEnroll onEnrolled={reload} />
        </Card>
      )
  }
}
