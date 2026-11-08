import { isPhoneSignIn } from '../../auth/phoneSignIn'
import { useT } from '../../i18n/I18n'

/**
 * After a passkey sign-in the browser reported as `cross-platform` (a phone over the QR code, or
 * a security key), the computer may be shared: say so, and offer to sign out (ADR 0019, owner
 * question 4). The server already caps that session at 24 hours.
 */
export function PhoneSignInNotice({ busy, onSignOut }: { busy: boolean; onSignOut: () => void }) {
  const t = useT()
  if (!isPhoneSignIn()) return null
  return (
    <div className="notice" data-kind="info" role="status">
      <p>{t('passkey.phoneNotice')}</p>
      <button type="button" className="button button--secondary" disabled={busy} onClick={onSignOut}>
        {t('account.signOut')}
      </button>
    </div>
  )
}
