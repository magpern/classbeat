import { useState, type FormEvent } from 'react'
import { adminError } from '../../api/admin'
import { approveInviteRequest, type InviteRequest, type Issued } from '../../api/adminInviteRequests'
import type { Plan } from '../../api/adminSettings'
import { SelectField } from '../../components/classes/fields'
import { Field, Notice, Section, useAction } from '../../components/forms'
import { Button } from '../../components/ui/button'
import { isReason, useAdminWrite } from './StepUp'

/**
 * Approve one request (ADR 0033 section 3): pick the tier the new account starts with (or none),
 * how long the invite lasts and whether to mail the code. The plan list is the one Settings uses,
 * and so is `mailConfigured` (#989): with mail off the toggle is not offered at all. A 409
 * `already_decided` (another admin decided it meanwhile) hands `STALE` to `onStale`, which
 * closes this form and reloads the list (#994): there is nothing left to approve. The admin
 * area is English only, and the list reloads itself, so the words say so (#999).
 */
const STALE = 'That request was decided meanwhile. The list now shows the current queue.'

export function ApproveRequest({
  request,
  plans,
  plansError,
  mailConfigured,
  onIssued,
  onStale,
  onCancel,
}: {
  request: InviteRequest
  /** Lowest rank first; null while loading. */
  plans: Plan[] | null
  plansError: string | null
  mailConfigured: boolean
  onIssued: (issued: Issued) => void
  /** The request was decided meanwhile; `words` say so. */
  onStale: (words: string) => void
  onCancel: () => void
}) {
  const write = useAdminWrite()
  const action = useAction()
  const [planId, setPlanId] = useState('')
  const [grantDays, setGrantDays] = useState('30')
  const [expiresDays, setExpiresDays] = useState('14')
  const [sendEmail, setSendEmail] = useState(false)

  const grant = Number(grantDays)
  const expires = Number(expiresDays)
  const valid =
    Number.isInteger(expires) &&
    expires >= 1 &&
    expires <= 30 &&
    (planId === '' || (Number.isInteger(grant) && grant >= 1 && grant <= 366))

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!valid) return
    const input = {
      planId: planId === '' ? null : planId,
      grantDays: grant,
      expiresDays: expires,
      sendEmail: mailConfigured && sendEmail,
    }
    const approve = async () => {
      try {
        return await write(() => approveInviteRequest(request.id, input))
      } catch (caught) {
        if (!isReason(caught, 409, 'already_decided')) throw caught
        onStale(STALE)
        return undefined
      }
    }
    void action
      .run(approve, adminError)
      .then((issued) => {
        if (issued !== undefined) onIssued(issued)
      })
  }

  return (
    <Section title={`Approve ${request.email}`}>
      {plansError !== null ? <Notice kind="error">{plansError}</Notice> : null}
      {action.error !== null ? <Notice kind="error">{action.error}</Notice> : null}
      <form onSubmit={submit} className="flex flex-col gap-2" aria-label="Approve this request">
        <SelectField
          label="Tier"
          value={planId}
          onChange={(e) => {
            setPlanId(e.target.value)
          }}
          hint="The plan the new account starts with. A plain invite gives the usual free start."
        >
          <option value="">No tier (plain invite)</option>
          {(plans ?? []).map((plan) => (
            <option key={plan.id} value={plan.id}>
              {plan.name}
            </option>
          ))}
        </SelectField>
        {planId !== '' ? (
          <Field
            label="Days the tier lasts"
            type="number"
            min={1}
            max={366}
            value={grantDays}
            onChange={(e) => {
              setGrantDays(e.target.value)
            }}
          />
        ) : null}
        <Field
          label="Invite valid for days"
          type="number"
          min={1}
          max={30}
          value={expiresDays}
          onChange={(e) => {
            setExpiresDays(e.target.value)
          }}
        />
        {mailConfigured ? (
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={sendEmail}
              onChange={(e) => {
                setSendEmail(e.target.checked)
              }}
            />
            Email the code to the requester
          </label>
        ) : (
          <p className="m-0 text-muted" data-testid="mail-off">
            Mail is not set up on the server, so send the code to the requester yourself.
          </p>
        )}
        <div className="flex flex-wrap gap-2">
          <Button type="submit" disabled={action.busy || !valid}>
            Approve and create code
          </Button>
          <Button variant="secondary" onClick={onCancel}>
            Cancel
          </Button>
        </div>
      </form>
    </Section>
  )
}
