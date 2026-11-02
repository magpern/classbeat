import { Link } from '../../router/Router'

/** Swedebeat or MOWL refused the stored sign-in: the user connects again on the account page. */
export function ReconnectPrompt() {
  return (
    <div className="notice" data-kind="error" role="alert" data-testid="reconnect-prompt">
      <p className="m-0">
        Swedebeat or MOWL no longer accepts the stored sign-in, so nothing was sent. Connect again, then
        come back to this class.
      </p>
      <p className="m-0">
        <Link to="/account#connections">Connect again on your account page</Link>
      </p>
    </div>
  )
}
