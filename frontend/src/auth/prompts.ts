import { createContext, useContext } from 'react'

/** The user closed a re-authentication or step-up prompt instead of answering it. */
export class PromptCancelled extends Error {
  constructor() {
    super('cancelled')
    this.name = 'PromptCancelled'
  }
}

/**
 * Runs `action`; when the server answers `reauth_required` or `stepup_required`, asks the
 * user (password, provider or code), then runs the action again.
 */
export type Guard = <T>(action: () => Promise<T>) => Promise<T>

export const GuardContext = createContext<Guard | null>(null)

export function useGuard(): Guard {
  const guard = useContext(GuardContext)
  if (guard === null) throw new Error('useGuard needs a GuardProvider')
  return guard
}

/**
 * Appended to the return address of a provider re-authentication; AccountPage shows the hint.
 * The fragment is kept, so the Account tab the user was on (`#data`) is the one they come back to.
 */
export function reauthReturnTo(path: string, search: string, hash = ''): string {
  const params = new URLSearchParams(search)
  params.set('oauth', 'reauthed')
  return `${path}?${params.toString()}${hash}`
}

