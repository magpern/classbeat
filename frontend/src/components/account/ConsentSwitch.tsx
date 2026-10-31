import { useId, useState } from 'react'
import { giveSpotifyConsent, revokeSpotifyConsent, type SpotifyStatus } from '../../api/spotify'
import { useGuard } from '../../auth/prompts'
import { Notice, useAction } from '../forms'
import { Button } from '../ui/button'
import { Switch } from '../ui/switch'

export const REVOKE_TEXT =
  'Revoking makes classbeat forget the working playlist and stop using it. The playlist stays in your Spotify, private, with its last songs; delete it there if you do not want it. Picking songs needs your consent again, and the next pick creates a new working playlist.'

/**
 * The working-playlist consent (ADR 0009 section 9) as a switch. The exact sentence the server
 * sends (`consent_text`, of `consent_version`) is shown above it. Switching on sends the same
 * `PUT /api/connections/spotify/pool-consent` as before, through the guard (recent
 * authentication and step-up when the server asks); switching off asks for confirmation first,
 * then sends `DELETE`. `onChanged` reloads the status.
 */
export function ConsentSwitch({ status, onChanged }: { status: SpotifyStatus; onChanged: () => Promise<void> }) {
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
            if (on) run(giveSpotifyConsent, 'Thanks. classbeat may use the working playlist.')
            else setConfirming(true)
          }}
        />
        <label htmlFor={id} className="font-medium">
          Allow the working playlist
        </label>
      </div>
      <p data-testid="spotify-consent-state" className="m-0 text-muted">
        {status.poolConsent ? 'You have allowed the working playlist.' : 'You have not allowed it yet.'}
      </p>
      {confirming ? (
        <div className="notice" role="group" aria-label="Confirm revoke">
          <p>{REVOKE_TEXT}</p>
          <div className="flex flex-wrap gap-2">
            <Button
              disabled={action.busy}
              onClick={() => {
                run(revokeSpotifyConsent, 'Your consent is withdrawn. The playlist stays in your Spotify.')
              }}
            >
              Yes, revoke my consent
            </Button>
            <Button
              variant="secondary"
              onClick={() => {
                setConfirming(false)
              }}
            >
              Keep my consent
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  )
}
