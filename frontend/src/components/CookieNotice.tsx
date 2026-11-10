import { useState } from 'react'
import { Trans, useT } from '../i18n/I18n'
import { Link } from '../router/Router'

export const COOKIE_NOTICE_KEY = 'cb-cookie-notice'

function alreadyDismissed(): boolean {
  // The build-time prerender has no window and no visitor: it renders no notice.
  if (typeof window === 'undefined') return true
  try {
    return window.localStorage.getItem(COOKIE_NOTICE_KEY) === '1'
  } catch {
    return false
  }
}

/**
 * A small, non-modal notice in the bottom-left corner. The app sets only the cookies it needs to
 * sign in and the language and theme in localStorage, so this informs and does not ask for consent.
 * Dismissal is kept in localStorage; with storage blocked it shows again on each page load.
 */
export function CookieNotice() {
  const t = useT()
  const [open, setOpen] = useState(() => !alreadyDismissed())
  if (!open) return null
  const dismiss = () => {
    setOpen(false)
    try {
      window.localStorage.setItem(COOKIE_NOTICE_KEY, '1')
    } catch {
      /* storage blocked: the notice returns on the next page load */
    }
  }
  return (
    <section className="cookie-notice" aria-label={t('cookies.label')} data-testid="cookie-notice">
      <p className="cookie-notice__text">
        {t('cookies.text')}{' '}
        <Trans
          id="cookies.more"
          slots={{ privacy: <Link to="/privacy">{t('legal.inline.privacy')}</Link> }}
        />
      </p>
      <button type="button" className="cookie-notice__ok" onClick={dismiss}>
        {t('cookies.ok')}
      </button>
    </section>
  )
}
