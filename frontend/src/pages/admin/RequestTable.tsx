import type { InviteRequest } from '../../api/adminInviteRequests'
import type { Plan } from '../../api/adminSettings'
import { Button } from '../../components/ui/button'
import { Chip } from '../../components/ui/chip'
import { dayOf } from './format'

// One page of invite requests as a table (ADR 0033). The visitor's text is rendered as text only.
// On the pending tab each row has a checkbox for the bulk decline and its own Approve and Decline.

const CELL = 'border-b border-line py-2 pr-4 align-top'

/** The tier in words, with the plan's name (the id only when the plan is not known). */
export function tierText(request: InviteRequest, plans: Plan[] | null): string {
  if (request.planId === null) return 'No tier'
  const name = plans?.find((p) => p.id === request.planId)?.name ?? request.planId
  return `${name} for ${String(request.grantDays ?? 0)} days`
}

export function RequestTable({
  label,
  rows,
  pending,
  plans,
  busy,
  selected,
  onSelect,
  onApprove,
  onDecline,
}: {
  label: string
  rows: InviteRequest[]
  pending: boolean
  plans: Plan[] | null
  busy: boolean
  selected: ReadonlySet<string>
  onSelect: (ids: string[], on: boolean) => void
  onApprove: (request: InviteRequest) => void
  onDecline: (request: InviteRequest) => void
}) {
  const everyId = rows.map((r) => r.id)
  const allOn = everyId.length > 0 && everyId.every((id) => selected.has(id))
  return (
    <div className="relative overflow-x-auto" tabIndex={0} role="region" aria-label={label}>
      <table className="w-full border-collapse text-left text-[0.9375rem]">
        <thead className="text-muted">
          <tr>
            {pending ? (
              <th className={`${CELL} font-medium`}>
                <input
                  type="checkbox"
                  aria-label="Select every request on this page"
                  checked={allOn}
                  disabled={busy}
                  onChange={(e) => {
                    onSelect(everyId, e.target.checked)
                  }}
                />
              </th>
            ) : null}
            <th className={`${CELL} font-medium`}>Email</th>
            <th className={`${CELL} font-medium`}>Heard about classbeat</th>
            <th className={`${CELL} font-medium`}>Instructor</th>
            <th className={`${CELL} font-medium`}>Asked</th>
            <th className={`${CELL} font-medium`}>{pending ? 'Account' : 'Decision'}</th>
            {pending ? (
              <th className={`${CELL} font-medium`}>
                <span className="sr-only">Actions</span>
              </th>
            ) : null}
          </tr>
        </thead>
        <tbody>
          {rows.map((request) => (
            <tr key={request.id}>
              {pending ? (
                <td className={CELL}>
                  <input
                    type="checkbox"
                    aria-label={`Select ${request.email}`}
                    checked={selected.has(request.id)}
                    disabled={busy}
                    onChange={(e) => {
                      onSelect([request.id], e.target.checked)
                    }}
                  />
                </td>
              ) : null}
              <td className={`${CELL} break-all`}>{request.email}</td>
              <td className={CELL}>{request.heardFrom ?? ''}</td>
              <td className={CELL}>{request.instructor ? 'Yes' : 'No'}</td>
              <td className={CELL}>{dayOf(request.createdAt)}</td>
              <td className={CELL}>
                {pending ? (
                  request.hasAccount ? (
                    <Chip tone="warn">Has an account</Chip>
                  ) : (
                    ''
                  )
                ) : (
                  <>
                    {tierText(request, plans)}
                    {request.invite !== null ? (
                      <>
                        {' '}
                        <Chip>{request.invite.state}</Chip>
                      </>
                    ) : null}
                    <br />
                    <span className="text-muted">
                      {request.decidedBy?.email ?? 'a deleted account'} on {dayOf(request.decidedAt)}
                    </span>
                  </>
                )}
              </td>
              {pending ? (
                <td className={CELL}>
                  <div className="flex gap-2">
                    <Button
                      disabled={busy}
                      onClick={() => {
                        onApprove(request)
                      }}
                    >
                      Approve
                    </Button>
                    <Button
                      variant="ghost"
                      disabled={busy}
                      onClick={() => {
                        onDecline(request)
                      }}
                    >
                      Decline
                    </Button>
                  </div>
                </td>
              ) : null}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
