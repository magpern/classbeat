import type { ReactNode } from 'react'

// Why the environment forbids a choice: shown next to the disabled control.
export const REASONS = {
  openSignup: 'Open sign-up can only be set on the server (CLASSBEAT_PUBLIC_SIGNUP).',
  signupHeld:
    'Public sign-up is on (CLASSBEAT_PUBLIC_SIGNUP) but held at invite only: Turnstile is off on the server (CLASSBEAT_TURNSTILE_ENABLED).',
  songPickingOff:
    'Song picking is switched off on the server (CLASSBEAT_SONG_PICKING). This setting has no effect until the server switches it on.',
  trialsOff: 'Trials are switched off on the server (CLASSBEAT_TRIAL_DAYS_MAX is 0).',
  trialPlan: 'A trial cannot be of a plan above Instructor.',
  billing: 'Billing does not exist yet. It comes with a later milestone and cannot be switched on here.',
  swedebeatOff:
    'Swedebeat is switched off on the server (CLASSBEAT_SWEDEBEAT_ENABLED). This setting has no effect until the server switches it on.',
}

/** A disabled control's reason, tied to it with `aria-describedby`. */
export function Reason({ id, children }: { id: string; children: ReactNode }) {
  return (
    <p id={id} className="m-0 text-[0.9375rem] text-muted">
      {children}
    </p>
  )
}
