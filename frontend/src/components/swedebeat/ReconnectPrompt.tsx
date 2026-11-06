import { useT } from '../../i18n/I18n'
import { Link } from '../../router/Router'

/** Swedebeat or MOWL refused the stored sign-in: the user connects again on the account page. */
export function ReconnectPrompt() {
  const t = useT()
  return (
    <div className="notice" data-kind="error" role="alert" data-testid="reconnect-prompt">
      <p className="m-0">{t('swreconnect.text')}</p>
      <p className="m-0">
        <Link to="/account#connections">{t('swreconnect.link')}</Link>
      </p>
    </div>
  )
}
