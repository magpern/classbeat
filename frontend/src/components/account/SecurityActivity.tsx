import { useEffect, useState } from 'react'
import { listActivity, type ActivityEvent, type ActivityKind } from '../../api/auth'
import { providerName } from '../../api/messages'
import type { CatalogueKey } from '../../i18n/en'
import { dateTime } from '../../i18n/format'
import { useT } from '../../i18n/I18n'
import { Section } from '../forms'
import { Button } from '../ui/button'

const LABELS: Record<ActivityKind, CatalogueKey> = {
  sign_in: 'activity.kind.sign_in',
  sign_in_failed: 'activity.kind.sign_in_failed',
  sign_out: 'activity.kind.sign_out',
  sign_out_all: 'activity.kind.sign_out_all',
  reauth: 'activity.kind.reauth',
  reauth_failed: 'activity.kind.reauth_failed',
  signup_confirmed: 'activity.kind.signup_confirmed',
  password_changed: 'activity.kind.password_changed',
  password_reset: 'activity.kind.password_reset',
  email_change_requested: 'activity.kind.email_change_requested',
  email_changed: 'activity.kind.email_changed',
  identity_linked: 'activity.kind.identity_linked',
  identity_unlinked: 'activity.kind.identity_unlinked',
  totp_enrolled: 'activity.kind.totp_enrolled',
  totp_replaced: 'activity.kind.totp_replaced',
  totp_removed: 'activity.kind.totp_removed',
  recovery_codes_regenerated: 'activity.kind.recovery_codes_regenerated',
  mfa_failed: 'activity.kind.mfa_failed',
  mfa_reset: 'activity.kind.mfa_reset',
  session_location_changed: 'activity.kind.session_location_changed',
}

/** The kinds that can name the provider they used (`{provider}` in the text). */
const WITH_PROVIDER: Partial<Record<ActivityKind, CatalogueKey>> = {
  sign_in: 'activity.kind.sign_in.provider',
  reauth: 'activity.kind.reauth.provider',
  reauth_failed: 'activity.kind.reauth_failed.provider',
  identity_linked: 'activity.kind.identity_linked.provider',
}

function useLabel() {
  const t = useT()
  return (event: ActivityEvent): string => {
    const withProvider = WITH_PROVIDER[event.kind]
    if (event.provider !== null && withProvider !== undefined) {
      return t(withProvider, { provider: providerName(event.provider) })
    }
    if (event.kind === 'sign_out' && event.otherSession) return t('activity.kind.sign_out.other')
    return t(LABELS[event.kind])
  }
}

/**
 * Security activity (ADR 0003 amendment of 2026-11-07, #678): the user's own sign-ins,
 * sign-outs and security changes of the last 30 days, newest first, with the address each came
 * from (as the data export holds it). "Show older" loads the next page.
 */
export function SecurityActivity() {
  const t = useT()
  const label = useLabel()
  const [events, setEvents] = useState<ActivityEvent[] | null>(null)
  const [next, setNext] = useState<string | null>(null)
  const [failed, setFailed] = useState(false)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    const controller = new AbortController()
    listActivity(null, controller.signal).then(
      (page) => {
        if (controller.signal.aborted) return
        setEvents(page.events)
        setNext(page.nextBefore)
      },
      () => {
        if (!controller.signal.aborted) setFailed(true)
      },
    )
    return () => {
      controller.abort()
    }
  }, [])

  const more = () => {
    if (next === null) return
    setLoading(true)
    listActivity(next).then(
      (page) => {
        setEvents((shown) => [...(shown ?? []), ...page.events])
        setNext(page.nextBefore)
        setLoading(false)
      },
      () => {
        setFailed(true)
        setLoading(false)
      },
    )
  }

  return (
    <Section title={t('activity.title')}>
      <p className="text-muted">{t('activity.intro')}</p>
      {failed ? <p className="text-muted">{t('activity.loadFailed')}</p> : null}
      {events === null && !failed ? <p>{t('common.loading')}</p> : null}
      {events !== null && events.length === 0 ? <p>{t('activity.empty')}</p> : null}
      {events !== null && events.length > 0 ? (
        <ul className="m-0 flex list-none flex-col gap-2 p-0" data-testid="security-activity">
          {events.map((event) => (
            <li key={event.id} className="min-w-0 break-words">
              {label(event)}
              <span className="block text-muted">
                {dateTime(event.at)}
                {event.ip !== null ? ` · ${t('activity.from', { ip: event.ip })}` : null}
              </span>
            </li>
          ))}
        </ul>
      ) : null}
      {next !== null && !failed ? (
        <p>
          <Button variant="secondary" disabled={loading} onClick={more}>
            {t('activity.more')}
          </Button>
        </p>
      ) : null}
    </Section>
  )
}
