import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError } from '../../api/client'
import {
  cancelMowlQr,
  mowlQrStatus,
  startMowlQr,
  type Connection,
  type MowlQrStatus,
  type MowlQrTicket,
} from '../../api/connections'
import { errorMessage } from '../../api/messages'
import { useGuard } from '../../auth/prompts'
import type { CatalogueKey } from '../../i18n/en'
import { useT } from '../../i18n/I18n'
import { t as translate } from '../../i18n/t'
import { Notice, useAction } from '../forms'

// "Scan with the MOWL app" (ADR 0024). The page polls classbeat's own API; it never talks to MOWL
// or its broker, and it never sees MOWL's ticket, the topic key or the token.

export const POLL_MS = 2000

const FAILURES: Record<string, CatalogueKey> = {
  upstream_unavailable: 'mowlqr.err.upstream_unavailable',
  rate_limited: 'mowlqr.err.rate_limited',
  unknown: 'mowlqr.err.unknown',
}

function failureText(reason: string | null): string {
  const known = reason !== null && Object.hasOwn(FAILURES, reason) ? FAILURES[reason] : undefined
  return translate(known ?? 'mowlqr.err.other')
}

function startError(error: unknown): string {
  if (error instanceof ApiError && error.status !== 429 && error.reason !== null) {
    const known = Object.hasOwn(FAILURES, error.reason) ? FAILURES[error.reason] : undefined
    if (known !== undefined) return translate(known)
  }
  return errorMessage(error)
}

function endText(status: MowlQrStatus): string {
  if (status.status === 'expired') return translate('mowlqr.expired')
  if (status.status === 'cancelled') return translate('mowlqr.cancelled')
  return failureText(status.reason)
}

function left(expiresAt: string, now: number): string {
  const end = new Date(expiresAt).getTime()
  const seconds = Number.isNaN(end) ? 0 : Math.max(0, Math.round((end - now) / 1000))
  return `${String(Math.floor(seconds / 60))}:${String(seconds % 60).padStart(2, '0')}`
}

/** The QR panel of the MOWL card, shown in the wider layouts only (a phone cannot scan itself). */
export function MowlQr({ onConnected }: { onConnected: (connection: Connection) => void }) {
  const t = useT()
  const guard = useGuard()
  const action = useAction()
  const [ticket, setTicket] = useState<MowlQrTicket | null>(null)
  const [ended, setEnded] = useState<string | null>(null)
  const [now, setNow] = useState(() => Date.now())
  // The running ticket: cancelled on the server when it is stopped or the panel goes away.
  const live = useRef<string | null>(null)

  const release = useCallback(() => {
    const id = live.current
    live.current = null
    if (id !== null) void cancelMowlQr(id).catch(() => undefined)
  }, [])

  useEffect(() => release, [release])

  useEffect(() => {
    if (ticket === null) return
    let stopped = false
    let timer: ReturnType<typeof setTimeout> | undefined
    const finish = (text: string | null) => {
      stopped = true
      live.current = null
      setTicket(null)
      setEnded(text)
    }
    const poll = () => {
      timer = setTimeout(() => {
        mowlQrStatus(ticket.ticketId).then(
          (status) => {
            if (stopped) return
            if (status.status === 'waiting') {
              poll()
            } else if (status.status === 'connected' && status.connection !== null) {
              finish(null)
              onConnected(status.connection)
            } else {
              finish(endText(status))
            }
          },
          (error: unknown) => {
            if (stopped) return
            // The wait is gone (the server restarted, or another tab started a new code).
            if (error instanceof ApiError && error.status === 404) finish(t('mowlqr.gone'))
            else poll() // a passing network problem; the code still ends at its expiry
          },
        )
      }, POLL_MS)
    }
    poll()
    const tick = setInterval(() => {
      setNow(Date.now())
    }, 1000)
    return () => {
      stopped = true
      clearTimeout(timer)
      clearInterval(tick)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `t` follows the language, not the poll
  }, [ticket, onConnected])

  const start = () => {
    setEnded(null)
    void action.run(async () => {
      release()
      const started = await guard(() => startMowlQr())
      live.current = started.ticketId
      setNow(Date.now())
      setTicket(started)
    }, startError)
  }

  const stop = () => {
    release()
    setTicket(null)
    setEnded(null)
  }

  return (
    <div data-testid="mowl-qr">
      <h3>{t('mowlqr.title')}</h3>
      {action.error ? <Notice kind="error">{action.error}</Notice> : null}
      {ticket ? (
        <>
          <p>{t('mowlqr.how')}</p>
          <img className="qr" src={ticket.image} alt={t('mowlqr.alt')} />
          <p className="muted" aria-live="polite" data-testid="mowl-qr-countdown">
            {t('mowlqr.countdown', { time: left(ticket.expiresAt, now) })}
          </p>
          <button className="button button--secondary" type="button" onClick={stop}>
            {t('mowlqr.stop')}
          </button>
        </>
      ) : (
        <>
          {ended ? <Notice kind="info">{ended}</Notice> : null}
          <p>{t('mowlqr.intro')}</p>
          <button className="button" type="button" disabled={action.busy} onClick={start}>
            {ended ? t('mowlqr.showNew') : t('mowlqr.show')}
          </button>
        </>
      )}
    </div>
  )
}
