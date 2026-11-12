import { useState, type FormEvent } from 'react'
import { asText, type Flag } from '../../api/adminSettings'
import { SelectField } from '../../components/classes/fields'
import { Notice } from '../../components/forms'
import { Button } from '../../components/ui/button'
import { REASONS, Reason } from './settingsReasons'

// The Settings forms of the plain flags (sign-up, song picking, Swedebeat), moved out of
// Settings.tsx (split on touch, #989). The sign-up form warns while invites issued with a tier
// (ADR 0033) are unused and sign-up is not invite-only, since they are then not honoured.

function TierInvitesWarning({ count, mode }: { count: number; mode: string | null }) {
  const invites = count === 1 ? '1 invite issued with a tier is' : `${String(count)} invites issued with a tier are`
  const effect =
    mode === 'closed'
      ? 'While sign-up is closed, nobody can use them.'
      : 'While sign-up is open, a new account may start without the tier its invite promised.'
  return (
    <div className="basis-full" data-testid="tier-invites-warning">
      <Notice kind="error">
        {invites} still unused. {effect} Set sign-up back to invite only before they expire, or tell their holders.
      </Notice>
    </div>
  )
}

export function SignupForm({
  item,
  unusedTierInvites,
  busy,
  onSave,
}: {
  item: Flag
  unusedTierInvites: number
  busy: boolean
  onSave: (v: string) => void
}) {
  const [mode, setMode] = useState(asText(item.stored) ?? 'invite')
  const effective = asText(item.effective)
  const submit = (event: FormEvent) => {
    event.preventDefault()
    onSave(mode)
  }
  return (
    <form onSubmit={submit} aria-label="Change the sign-up mode" className="flex flex-wrap items-end gap-2">
      <SelectField label="Sign-up mode" value={mode} aria-describedby="reason-open-signup" onChange={(e) => { setMode(e.target.value) }}>
        <option value="invite">Invite only</option>
        <option value="closed">Closed</option>
        <option value="open" disabled>
          Open (server only)
        </option>
      </SelectField>
      <Button type="submit" disabled={busy}>
        Save
      </Button>
      <Reason id="reason-open-signup">{REASONS.openSignup}</Reason>
      {item.environmentNote === 'turnstile_off' ? <Reason id="reason-signup-held">{REASONS.signupHeld}</Reason> : null}
      {effective !== 'invite' && unusedTierInvites > 0 ? (
        <TierInvitesWarning count={unusedTierInvites} mode={effective} />
      ) : null}
    </form>
  )
}

export function SongForm({ item, busy, onSave }: { item: Flag; busy: boolean; onSave: (v: string) => void }) {
  const [value, setValue] = useState(asText(item.stored) ?? 'granted')
  const forbidden = item.environment === 'off'
  const submit = (event: FormEvent) => {
    event.preventDefault()
    onSave(value)
  }
  return (
    <form onSubmit={submit} aria-label="Change song picking" className="flex flex-wrap items-end gap-2">
      <SelectField
        label="Song picking for"
        value={value}
        disabled={forbidden}
        aria-describedby={forbidden ? 'reason-song-picking' : undefined}
        onChange={(e) => {
          setValue(e.target.value)
        }}
      >
        <option value="off">Nobody</option>
        <option value="granted">Users granted the feature</option>
        <option value="on">Everyone</option>
      </SelectField>
      <Button type="submit" disabled={busy || forbidden}>
        Save
      </Button>
      {forbidden ? <Reason id="reason-song-picking">{REASONS.songPickingOff}</Reason> : null}
    </form>
  )
}

export function SwedebeatForm({ item, busy, onSave }: { item: Flag; busy: boolean; onSave: (v: string) => void }) {
  const [value, setValue] = useState(asText(item.stored) ?? 'on')
  const forbidden = item.environment === 'off'
  const submit = (event: FormEvent) => {
    event.preventDefault()
    onSave(value)
  }
  return (
    <form onSubmit={submit} aria-label="Change Swedebeat" className="flex flex-wrap items-end gap-2">
      <SelectField
        label="Swedebeat sign-in for"
        value={value}
        disabled={forbidden}
        aria-describedby={forbidden ? 'reason-swedebeat' : undefined}
        onChange={(e) => {
          setValue(e.target.value)
        }}
      >
        <option value="off">Nobody</option>
        <option value="on">Everyone</option>
      </SelectField>
      <Button type="submit" disabled={busy || forbidden}>
        Save
      </Button>
      {forbidden ? <Reason id="reason-swedebeat">{REASONS.swedebeatOff}</Reason> : null}
    </form>
  )
}
