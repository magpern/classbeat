import { useEffect, useState } from 'react'
import { getPlayerConsent, withdrawPlayerConsent, type PlayerConsent as Consent } from '../../api/playerConsent'
import { date } from '../../i18n/format'
import { useT } from '../../i18n/I18n'
import { Notice, Section, useAction } from '../forms'
import { Button } from '../ui/button'

/**
 * The player's consent on the account page (ADR 0034 sections 1 and 6): when the user allowed
 * classbeat-player to sign them in, and a button to withdraw it. Withdrawing does not disconnect
 * the player; its next sign-in asks again. Shown only while the sign-in provider is on.
 */
export function PlayerConsent() {
  const t = useT()
  const [consent, setConsent] = useState<Consent | null>(null)
  const [failed, setFailed] = useState(false)
  const [done, setDone] = useState(false)
  const action = useAction()

  useEffect(() => {
    const controller = new AbortController()
    getPlayerConsent(controller.signal).then(
      (found) => {
        if (!controller.signal.aborted) setConsent(found)
      },
      () => {
        if (!controller.signal.aborted) setFailed(true)
      },
    )
    return () => {
      controller.abort()
    }
  }, [])

  const withdraw = () => {
    setDone(false)
    void action.run(async () => {
      await withdrawPlayerConsent()
      setConsent(await getPlayerConsent())
      setDone(true)
    })
  }

  if (failed) {
    return (
      <Section title={t('player.consent.title')}>
        <Notice kind="error">{t('error.generic')}</Notice>
      </Section>
    )
  }
  if (consent === null || !consent.enabled) return null
  return (
    <Section title={t('player.consent.title')}>
      <p data-testid="player-consent">
        {consent.consentAt !== null
          ? t('player.consent.given', { date: date(consent.consentAt) })
          : t('player.consent.none')}
      </p>
      {done ? <Notice kind="success">{t('player.consent.withdrawn')}</Notice> : null}
      {action.error ? <Notice kind="error">{action.error}</Notice> : null}
      {consent.consentAt !== null ? (
        <>
          <p className="text-muted">{t('player.consent.help')}</p>
          <p>
            <Button variant="secondary" disabled={action.busy} onClick={withdraw}>
              {t('player.consent.withdraw')}
            </Button>
          </p>
        </>
      ) : null}
    </Section>
  )
}
