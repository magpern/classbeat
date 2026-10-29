import type { DesignInput } from '../api/jobs'

// The form's input is kept per job in this tab's session storage, so a failed or stopped design
// can be started again without typing the brief twice. Only the user's own form values are kept
// (no token, no password); storage can be blocked or full, so every access is guarded.

const PREFIX = 'classbeat.design-input.'

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
