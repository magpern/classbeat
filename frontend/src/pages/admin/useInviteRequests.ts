import { useCallback, useEffect, useState } from 'react'
import { adminError } from '../../api/admin'
import { listInviteRequests, type InviteRequest, type RequestState } from '../../api/adminInviteRequests'
import { getSettings, type Plan } from '../../api/adminSettings'
import { isReason, useMfaRequired } from './StepUp'

// The reads of the Invite requests screen (ADR 0033, #989): one page of requests at a time with
// the server's count of the whole pending queue, and the Settings facts the screen needs (the
// plans, for their names and the tier choice, and whether mail can go out), read once.

export const PAGE = 50

export interface Requests {
  items: InviteRequest[] | null
  next: number | null
  /** The whole pending queue (the server's count); null until the first page has loaded. */
  pending: number | null
  error: string | null
  reload: () => void
}

export function useRequests(state: RequestState, offset: number): Requests {
  const onMfaRequired = useMfaRequired()
  const [items, setItems] = useState<InviteRequest[] | null>(null)
  const [next, setNext] = useState<number | null>(null)
  const [pending, setPending] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [round, setRound] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    listInviteRequests(state, offset, PAGE, controller.signal).then(
      (found) => {
        setError(null)
        setItems(found.requests)
        setNext(found.nextOffset)
        setPending(found.pending)
      },
      (caught: unknown) => {
        if (controller.signal.aborted) return
        if (isReason(caught, 403, 'mfa_required')) onMfaRequired()
        else setError(adminError(caught))
      },
    )
    return () => {
      controller.abort()
    }
  }, [state, offset, round, onMfaRequired])

  const reload = useCallback(() => {
    setRound((n) => n + 1)
  }, [])
  return { items, next, pending, error, reload }
}

export interface Facts {
  /** Lowest rank first; null while loading or after an error. */
  plans: Plan[] | null
  mailConfigured: boolean
  error: string | null
}

export function useSettingsFacts(): Facts {
  const onMfaRequired = useMfaRequired()
  const [facts, setFacts] = useState<Facts>({ plans: null, mailConfigured: false, error: null })

  useEffect(() => {
    const controller = new AbortController()
    getSettings(controller.signal).then(
      (view) => {
        const plans = [...view.plans].sort((a, b) => a.rank - b.rank)
        setFacts({ plans, mailConfigured: view.readOnly.mailConfigured, error: null })
      },
      (caught: unknown) => {
        if (controller.signal.aborted) return
        if (isReason(caught, 403, 'mfa_required')) onMfaRequired()
        else setFacts({ plans: null, mailConfigured: false, error: adminError(caught) })
      },
    )
    return () => {
      controller.abort()
    }
  }, [onMfaRequired])
  return facts
}
