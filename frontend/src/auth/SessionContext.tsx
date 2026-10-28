import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'
import { getSession, type SessionState } from '../api/auth'

type Status = 'loading' | 'ready' | 'error'

interface SessionValue {
  status: Status
  session: SessionState | null
  /** Fetches the session again (after sign-in, re-authentication, MFA ...). */
  refresh: () => Promise<SessionState | null>
}

const SessionContext = createContext<SessionValue | null>(null)

export function SessionProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<Status>('loading')
  const [session, setSession] = useState<SessionState | null>(null)

  const refresh = useCallback(async () => {
    try {
      const next = await getSession()
      setSession(next)
      setStatus('ready')
      return next
    } catch {
      setStatus('error')
      return null
    }
  }, [])

  useEffect(() => {
    let current = true
    getSession().then(
      (next) => {
        if (!current) return
        setSession(next)
        setStatus('ready')
      },
      () => {
        if (current) setStatus('error')
      },
    )
    return () => {
      current = false
    }
  }, [])

  const value = useMemo(() => ({ status, session, refresh }), [status, session, refresh])
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>
}

export function useSession(): SessionValue {
  const value = useContext(SessionContext)
  if (value === null) throw new Error('useSession needs a SessionProvider')
  return value
}
