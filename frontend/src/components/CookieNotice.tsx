import { useRef, useState } from 'react'
import { usePublishHeight } from '../hooks/usePublishHeight'
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
 * A small, non-modal notice: a card in the bottom-left corner on a wide screen, a compact bar at the
 * foot of the page on a phone (above the tab bar when there is one). The bar takes room: it publishes
 * its height (`--cb-notice-measured`) and the page, the Landing hero and the other fixed bottom bars
 * leave that room, so it never covers a primary action (#1273, app.css). The app sets only the cookies
 * it needs to sign in and the language and theme in localStorage, so this informs and does not ask for
 * consent. Dismissal is kept in localStorage; with storage blocked it shows again on each page load.
 */
export function CookieNotice() {
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
  return <Notice onDismiss={dismiss} />
}

function Notice({ onDismiss }: { onDismiss: () => void }) {
  const t = useT()
  const ref = useRef<HTMLElement>(null)
  usePublishHeight(ref, '--cb-notice-measured')
  return (
    <section ref={ref} className="cookie-notice" aria-label={t('cookies.label')} data-testid="cookie-notice">
      <p className="cookie-notice__text">
        {t('cookies.text')}{' '}
        <Trans
          id="cookies.more"
          slots={{ privacy: <Link to="/privacy">{t('legal.inline.privacy')}</Link> }}
        />
      </p>
      <button type="button" className="cookie-notice__ok" onClick={onDismiss}>
        {t('cookies.ok')}
      </button>
    </section>
  )
}
