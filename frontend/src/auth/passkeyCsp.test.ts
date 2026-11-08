import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

// The CSP allows no inline script or style (ADR 0019 section 9). The passkey UI is plain
// React with class names; this keeps it that way and each new file small and single-purpose.
const FILES = [
  'api/passkeys.ts',
  'api/activity.ts',
  'auth/webauthn.ts',
  'auth/passkeyCeremony.ts',
  'auth/phoneSignIn.ts',
  'auth/GuardPrompts.tsx',
  'components/auth/PasskeyReauth.tsx',
  'components/auth/PasskeySignIn.tsx',
  'components/auth/usePasskeyAutofill.ts',
  'components/account/Passkeys.tsx',
  'components/account/PasskeyRow.tsx',
  'components/shell/PhoneSignInNotice.tsx',
]

describe('passkey UI under the CSP', () => {
  it.each(FILES)('%s has no inline style, script or raw HTML and stays under 300 lines', (file) => {
    const source = readFileSync(join(import.meta.dirname, '..', file), 'utf8')
    expect(source).not.toMatch(/\sstyle=|<style|<script|dangerouslySetInnerHTML|innerHTML|\beval\(|new Function\(/)
    expect(source.split('\n').length).toBeLessThan(300)
  })
})
