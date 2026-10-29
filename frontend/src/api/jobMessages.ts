// Plain sentences for a job's failure categories (ADR 0005 section 4) and stages. The category
// code is never shown, and no provider or model text ever reaches these pages.

export interface FailureText {
  title: string
  detail: string
  /** The user can fix it on the account page. */
  account?: boolean
}

const FAILURES: Record<string, FailureText> = {
  upstream_unavailable: {
    title: 'MOWL could not be reached',
    detail: 'classbeat tried several times. Your class was not designed. Try again in a few minutes.',
  },
  rate_limited: {
    title: 'MOWL asked classbeat to slow down',
    detail: 'Your class was not designed. Wait a few minutes and try again.',
  },
  model_unavailable: {
    title: 'The designer is busy',
    detail: 'The service that designs the class could not take the request. Try again in a few minutes.',
  },
  lease_lost: {
    title: 'The design was interrupted',
    detail: 'It was interrupted too many times. Try again.',
  },
  operation_busy: {
    title: 'Another design is using this playlist',
    detail: 'Another design of yours was still adding the same playlist to MOWL. Try again in a moment.',
  },
  not_connected: {
    title: 'MOWL is not connected',
    detail: 'Connect MOWL on your account page, then start again.',
    account: true,
  },
  needs_reconnect: {
    title: 'MOWL no longer accepts the stored sign-in',
    detail: 'Connect MOWL again on your account page, then start again.',
    account: true,
  },
  key_unavailable: {
    title: 'The stored MOWL sign-in could not be read',
    detail: 'Connect MOWL again on your account page, then start again.',
    account: true,
  },
  decrypt_failed: {
    title: 'The stored MOWL sign-in could not be read',
    detail: 'Connect MOWL again on your account page, then start again.',
    account: true,
  },
  invalid_input: {
    title: 'The request could not be used',
    detail: 'Check the form and start again.',
  },
  account_disabled: {
    title: 'Your account cannot start designs right now',
    detail: 'Contact the person who invited you.',
  },
  model_refused: {
    title: 'The designer declined this brief',
    detail: 'Change the description and start again.',
  },
  model_config: {
    title: 'classbeat cannot design classes right now',
    detail: 'This is a problem on our side. Try again later.',
  },
  invalid_output: {
    title: 'The designer did not produce a usable class',
    detail: 'Try again, or simplify the description.',
  },
  limit_exceeded: {
    title: 'This brief needed more work than one design may use',
    detail: 'Try a shorter or simpler class.',
  },
  outbound_blocked: {
    title: 'Something went wrong on our side',
    detail: 'Try again later.',
  },
  internal: {
    title: 'Something went wrong on our side',
    detail: 'Try again later.',
  },
}

const FALLBACK: FailureText = {
  title: 'The design did not finish',
  detail: 'Try again later.',
}

/** The text of a failure category; `uncertain_write` has its own component. */
export function failureText(category: string | null): FailureText {
  return category !== null && Object.hasOwn(FAILURES, category) ? FAILURES[category]! : FALLBACK
}

const STAGES: Record<string, string> = {
  music: 'Reading your playlist',
  design: 'Designing the class',
}

export function stageLabel(stage: string | null): string {
  return stage !== null && Object.hasOwn(STAGES, stage) ? STAGES[stage]! : 'Getting started'
}

const RETRIES: Record<string, string> = {
  upstream_unavailable: 'MOWL did not answer. Trying again shortly.',
  rate_limited: 'MOWL asked classbeat to slow down. Trying again shortly.',
  model_unavailable: 'The designer is busy. Trying again shortly.',
}

export function retryLabel(category: string | null): string {
  return category !== null && Object.hasOwn(RETRIES, category)
    ? RETRIES[category]!
    : 'Something went wrong. Trying again shortly.'
}
