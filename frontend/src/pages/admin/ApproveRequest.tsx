import { useEffect, useState, type FormEvent } from 'react'
import { adminError } from '../../api/admin'
import { approveInviteRequest, type InviteRequest, type Issued } from '../../api/adminInviteRequests'
import { getSettings, type Plan } from '../../api/adminSettings'
import { SelectField } from '../../components/classes/fields'
import { Field, Notice, Section, useAction } from '../../components/forms'
import { Button } from '../../components/ui/button'
import { isReason, useAdminWrite, useMfaRequired } from './StepUp'

/**
 * Approve one request (ADR 0033 section 3): pick the tier the new account starts with (or none),
 * how long the invite lasts and whether to mail the code. The plan list is the one Settings uses.
 * The server cannot tell this page whether mail is set up, so the toggle is always offered and
 * `mail_off` is explained after the fact by the caller.
 */
export function ApproveRequest({
  request,
  onIssued,
  onCancel,
}: {
  request: InviteRequest
  onIssued: (issued: Issued) => void
  onCancel: () => void
}) {
  const write = useAdminWrite()
  const onMfaRequired = useMfaRequired()
  const action = useAction()
  const [plans, setPlans] = useState<Plan[] | null>(null)
  const [plansError, setPlansError] = useState<string | null>(null)
  const [planId, setPlanId] = useState('')
  const [grantDays, setGrantDays] = useState('30')
  const [expiresDays, setExpiresDays] = useState('14')
  const [sendEmail, setSendEmail] = useState(false)

  useEffect(() => {
    const controller = new AbortController()
    getSettings(controller.signal).then(
      (view) => {
        setPlans([...view.plans].sort((a, b) => a.rank - b.rank))
      },
      (caught: unknown) => {
        if (controller.signal.aborted) return
        if (isReason(caught, 403, 'mfa_required')) onMfaRequired()
        else setPlansError(adminError(caught))
      },
    )
    return () => {
      controller.abort()
    }
  }, [onMfaRequired])

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
      sendEmail,
    }
    void action
      .run(() => write(() => approveInviteRequest(request.id, input)), adminError)
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
