import type { DesignInput } from '../api/jobs'

// The form's input is kept per job in this tab's session storage, so a failed or stopped design
// can be started again without typing the brief twice. Only the user's own form values are kept
// (no token, no password); storage can be blocked or full, so every access is guarded.

const PREFIX = 'classbeat.design-input.'
const KEY_PREFIX = 'classbeat.design-again-key.'

export function rememberInput(jobId: string, input: DesignInput): void {
  try {
    window.sessionStorage.setItem(PREFIX + jobId, JSON.stringify(input))
  } catch {
    // Without storage the page offers the form again instead.
  }
}

export function recallInput(jobId: string): DesignInput | null {
  try {
    const raw = window.sessionStorage.getItem(PREFIX + jobId)
    if (raw === null) return null
    const parsed: unknown = JSON.parse(raw)
    if (typeof parsed !== 'object' || parsed === null) return null
    const input = parsed as Partial<DesignInput>
    if (input.brief === undefined || input.music === undefined) return null
    if (typeof input.mowl_connection_id !== 'string') return null
    return input as DesignInput
  } catch {
    return null
  }
}

/** A key for one user action (`Idempotency-Key`: 16 to 64 of A-Za-z0-9_-). */
export function newIdempotencyKey(): string {
  return crypto.randomUUID()
}

export function forgetInput(jobId: string): void {
  try {
    window.sessionStorage.removeItem(PREFIX + jobId)
    window.sessionStorage.removeItem(KEY_PREFIX + jobId)
  } catch {
    // Nothing to forget without storage.
  }
}

/** Removes every remembered form input and key: on sign-out and when the session is gone. */
export function clearSubmittedInputs(): void {
  try {
    const stale: string[] = []
    for (let i = 0; i < window.sessionStorage.length; i++) {
      const name = window.sessionStorage.key(i)
      if (name !== null && (name.startsWith(PREFIX) || name.startsWith(KEY_PREFIX))) stale.push(name)
    }
    for (const name of stale) window.sessionStorage.removeItem(name)
  } catch {
    // Nothing to clear without storage.
  }
}

/**
 * The key for starting job `jobId` again. It is kept until that attempt succeeds, so a click
 * repeated after a lost answer returns the job the first click made instead of a second one.
 */
export function designAgainKey(jobId: string): string {
  try {
    const kept = window.sessionStorage.getItem(KEY_PREFIX + jobId)
    if (kept !== null && /^[A-Za-z0-9_-]{16,64}$/.test(kept)) return kept
    const made = newIdempotencyKey()
    window.sessionStorage.setItem(KEY_PREFIX + jobId, made)
    return made
  } catch {
    return newIdempotencyKey()
  }
}
