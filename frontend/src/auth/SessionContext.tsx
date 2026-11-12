import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import { getSession, type SessionState } from '../api/auth'
import { clearSubmittedInputs } from '../lib/submittedInput'
import { setPhoneSignIn } from './phoneSignIn'

type Status = 'loading' | 'ready' | 'error'

interface SessionValue {
  status: Status
  session: SessionState | null
  /** Fetches the session again (after sign-in, re-authentication, MFA ...). */
  refresh: () => Promise<SessionState | null>
}

function signedOut(): void {
  clearSubmittedInputs()
  setPhoneSignIn(false)
}

const SessionContext = createContext<SessionValue | null>(null)

export function SessionProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<Status>('loading')
  const [session, setSession] = useState<SessionState | null>(null)

  // Reads can overlap (the first read on mount, one after sign-in, one after sign-out) and answer out of
  // order. Only the read that started last may set the state: an older answer that arrives late would
  // bring a signed-out browser back to a signed-in view (the account menu staying after Sign out).
  const started = useRef(0)

  const refresh = useCallback(async () => {
    const mine = ++started.current
    try {
      const next = await getSession()
      if (mine === started.current) {
        if (!next.authenticated) signedOut()
        setSession(next)
        setStatus('ready')
      }
      return next
    } catch {
      if (mine === started.current) setStatus('error')
      return null
    }
  }, [])

  useEffect(() => {
    let current = true
    const mine = ++started.current
    getSession().then(
      (next) => {
        if (!current || mine !== started.current) return
        if (!next.authenticated) signedOut()
        setSession(next)
        setStatus('ready')
      },
      () => {
        if (current && mine === started.current) setStatus('error')
      },
    )
    return () => {
      current = false
    }
  }, [])

  const value = useMemo(() => ({ status, session, refresh }), [status, session, refresh])
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>
}

/** Whether a signed-in session is known. Never throws: the router asks, and tests mount it alone. */
export function useIsAuthenticated(): boolean {
  return useContext(SessionContext)?.session?.authenticated === true
}

export function useSession(): SessionValue {
  const value = useContext(SessionContext)
  if (value === null) throw new Error('useSession needs a SessionProvider')
  return value
}
