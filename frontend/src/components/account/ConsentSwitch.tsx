import { useId, useState } from 'react'
import { giveSpotifyConsent, revokeSpotifyConsent, type SpotifyStatus } from '../../api/spotify'
import { useGuard } from '../../auth/prompts'
import { useT } from '../../i18n/I18n'
import { Notice, useAction } from '../forms'
import { Button } from '../ui/button'
import { Details } from '../ui/details'
import { Switch } from '../ui/switch'

/**
 * The working-playlist consent (ADR 0009 section 9) as a switch. The exact sentence the server
 * sends (`consent_text`, of `consent_version`) is shown above it. Switching on sends the same
 * `PUT /api/connections/spotify/pool-consent` as before, through the guard (recent
 * authentication and step-up when the server asks); switching off asks for confirmation first,
 * then sends `DELETE`. `onChanged` reloads the status.
 */
export function ConsentSwitch({ status, onChanged }: { status: SpotifyStatus; onChanged: () => Promise<void> }) {
  const t = useT()
  const guard = useGuard()
  const action = useAction()
  const id = useId()
  const [confirming, setConfirming] = useState(false)
  const [note, setNote] = useState<string | null>(null)

  const run = (task: () => Promise<void>, done: string) => {
    setNote(null)
    setConfirming(false)
    void action.run(async () => {
      await guard(task)
      await onChanged()
      setNote(done)
    })
  }

  return (
    <div className="flex flex-col gap-3">
      <blockquote data-testid="spotify-consent-text" className="m-0 border-l-4 border-line pl-4 text-text">
        {status.consentText}
      </blockquote>
      {note ? <Notice kind="success">{note}</Notice> : null}
      {action.error ? <Notice kind="error">{action.error}</Notice> : null}
      <div className="flex items-center gap-3">
        <Switch
          id={id}
          checked={status.poolConsent}
          disabled={action.busy || confirming}
          onCheckedChange={(on) => {
            if (on) run(giveSpotifyConsent, t('consent.given'))
            else setConfirming(true)
          }}
        />
        <label htmlFor={id} className="font-medium">
          {t('consent.label')}
        </label>
      </div>
      <p data-testid="spotify-consent-state" className="m-0 text-muted">
        {status.poolConsent ? t('consent.stateOn') : t('consent.stateOff')}
      </p>
      {confirming ? (
        <div className="notice" role="group" aria-label={t('consent.confirmRevoke')}>
          <p>{t('consent.revokeText')}</p>
          <Details>
            <p className="m-0">{t('consent.revokeDetails')}</p>
          </Details>
          <div className="flex flex-wrap gap-2">
            <Button
              disabled={action.busy}
              onClick={() => {
                run(revokeSpotifyConsent, t('consent.withdrawn'))
              }}
            >
              {t('consent.yesRevoke')}
            </Button>
            <Button
              variant="secondary"
              onClick={() => {
                setConfirming(false)
              }}
            >
              {t('consent.keep')}
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  )
}
