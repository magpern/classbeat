// Owner question 4 of ADR 0019: after a sign-in with a passkey kept on a phone or a security key
// (the browser said `cross-platform`), the page reminds the user to sign out on a shared
// computer. The fact lives in sessionStorage only (this tab, gone when it closes) and is cleared
// whenever the session is found signed out. Storage can be blocked: every access is guarded.

const KEY = 'classbeat.phoneSignIn'

export function setPhoneSignIn(on: boolean): void {
  try {
    if (on) sessionStorage.setItem(KEY, '1')
    else sessionStorage.removeItem(KEY)
  } catch {
    // Without storage the notice is simply not shown.
  }
}

export function isPhoneSignIn(): boolean {
  try {
    return sessionStorage.getItem(KEY) === '1'
  } catch {
    return false
  }
}
