import { useEffect, useRef } from 'react'
import { ApiError } from '../../api/client'
import { passkeyErrorMessage, signInWithPasskey, type SignInOutcome } from '../../auth/passkeyCeremony'
import { conditionalAvailable, PasskeyCancelled } from '../../auth/webauthn'

/** Wait before restarting a failed autofill ceremony; it doubles with each failure in a row. */
export const AUTOFILL_RETRY_MS = 1000
/** Failures in a row after which autofill stops for good (the button still works). */
export const AUTOFILL_MAX_FAILURES = 5

interface Options {
  /** True while the button's own ceremony runs: autofill stops, and starts again after it. */
  paused: boolean
  onSignedIn: (outcome: SignInOutcome) => Promise<void>
  /** A message the user should see; autofill otherwise fails quietly. */
  onError: (message: string | null) => void
}

/** Resolves once the page is visible, or at once when `signal` aborts (its listener goes too). */
function visible(signal: AbortSignal): Promise<void> {
  if (signal.aborted || document.visibilityState === 'visible') return Promise.resolve()
  return new Promise((resolve) => {
    const done = () => {
      document.removeEventListener('visibilitychange', check)
      signal.removeEventListener('abort', done)
      resolve()
    }
    const check = () => {
      if (document.visibilityState === 'visible') done()
    }
    document.addEventListener('visibilitychange', check)
    signal.addEventListener('abort', done)
  })
}

/** 429 and 503 (ADR 0019 section 6) and 404 `feature_off` say "stop asking". */
function stops(error: unknown): boolean {
  return error instanceof ApiError && (error.status === 429 || error.status === 503 || error.status === 404)
}

/**
 * Passkey autofill on the email field (conditional mediation, ADR 0019 section 5). The ceremony
 * restarts when its challenge expires, but only while the page is visible, so a forgotten tab
 * does not start one every five minutes. It stops on a rate limit or a busy server, and after a
 * few failures in a row.
 */
export function usePasskeyAutofill({ paused, onSignedIn, onError }: Options): void {
  const signedIn = useRef(onSignedIn)
  const failed = useRef(onError)
  useEffect(() => {
    signedIn.current = onSignedIn
    failed.current = onError
  })

  useEffect(() => {
    if (paused) return
    const controller = new AbortController()
    const { signal } = controller
    const pause = (ms: number) =>
      new Promise<void>((resolve) => {
        const timer = setTimeout(resolve, ms)
        signal.addEventListener('abort', () => {
          clearTimeout(timer)
          resolve()
        })
      })

    async function loop(): Promise<void> {
      if (!(await conditionalAvailable()) || signal.aborted) return
      let failures = 0
      while (!signal.aborted) {
        await visible(signal)
        if (signal.aborted) return
        try {
          const outcome = await signInWithPasskey({ conditional: true, signal })
          await signedIn.current(outcome)
          return
        } catch (error) {
          if (signal.aborted) return
          if (stops(error)) {
            failed.current(passkeyErrorMessage(error))
            return
          }
          // A challenge that ran out (the browser's timeout, or the server's `passkey_failed`)
          // is not worth a message; anything else the user may need to read.
          if (!(error instanceof PasskeyCancelled) && !(error instanceof ApiError && error.reason === 'passkey_failed')) {
            failed.current(passkeyErrorMessage(error))
          }
          failures += 1
          if (failures >= AUTOFILL_MAX_FAILURES) return
          await pause(AUTOFILL_RETRY_MS * 2 ** (failures - 1))
        }
      }
    }
    void loop()
    return () => {
      controller.abort()
    }
  }, [paused])
}
