import { useCallback, useEffect, useState } from 'react'
import { adminError } from '../../api/admin'
import {
  PENDING_CAP,
  declineInviteRequest,
  listAllPending,
  listInviteRequests,
  type InviteRequest,
  type Issued,
  type RequestState,
} from '../../api/adminInviteRequests'
import { Notice, Section, useAction } from '../../components/forms'
import { Button } from '../../components/ui/button'
import { Chip } from '../../components/ui/chip'
import { SegmentedControl } from '../../components/ui/segmented-control'
import { ApproveRequest } from './ApproveRequest'
import { dayOf } from './format'
import { OneTimeCode } from './OneTimeCode'
import { Pager } from './Pager'
import { isReason, useAdminWrite, useMfaRequired } from './StepUp'

const CELL = 'border-b border-line py-2 pr-4 align-top'
const PAGE = 50

const TABS = [
  { value: 'pending', label: 'Pending' },
  { value: 'issued', label: 'Issued' },
  { value: 'declined', label: 'Declined' },
] as const

function useRequests(state: RequestState, offset: number) {
  const onMfaRequired = useMfaRequired()
  const [items, setItems] = useState<InviteRequest[] | null>(null)
  const [next, setNext] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [round, setRound] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    const load =
      state === 'pending'
        ? listAllPending(controller.signal).then((all) => ({
            requests: all,
            nextOffset: null,
          }))
        : listInviteRequests(state, offset, PAGE, controller.signal)
    load.then(
      (found) => {
        setError(null)
        setItems(found.requests)
        setNext(found.nextOffset)
      },
      (caught: unknown) => {
        if (controller.signal.aborted) return
        if (isReason(caught, 403, 'mfa_required')) onMfaRequired()
        else setError(adminError(caught))
      },
    )
    return () => {
      controller.abort()
    }
  }, [state, offset, round, onMfaRequired])

  const reload = useCallback(() => {
    setRound((n) => n + 1)
  }, [])
  return { items, next, error, reload }
}

function deliveryNote(issued: Issued): string {
  const to = issued.request.email
  if (issued.email === 'queued') return `The code is also being emailed to ${to}.`
  if (issued.email === 'mail_off') {
    return `Mail is not set up on the server, so nothing was emailed. Send this code to ${to} yourself.`
  }
  return `Send this code to ${to} yourself.`
}

function tierText(request: InviteRequest): string {
  if (request.planId === null) return 'No tier'
  return `${request.planId} for ${String(request.grantDays ?? 0)} days`
}

/**
 * Invite requests (ADR 0033): who asked for an invite, and the admin's decision. Approving makes
 * one invite code, optionally with a tier, and shows it once. The server has no bulk decline, so
 * each request is decided on its own; the pending count against the server's cap is shown.
 */
export function InviteRequests() {
  const write = useAdminWrite()
  const action = useAction()
  const [state, setState] = useState<RequestState>('pending')
  const [offset, setOffset] = useState(0)
  const [approving, setApproving] = useState<InviteRequest | null>(null)
  const [issued, setIssued] = useState<Issued | null>(null)
  const list = useRequests(state, offset)

  const decline = (request: InviteRequest) => {
    // A 409 (decided meanwhile) shows its text, and the reload shows the current queue either way.
    void action.run(() => write(() => declineInviteRequest(request.id)), adminError).then(list.reload)
  }

  const rows = list.items
  const pending = state === 'pending'
  return (
    <div className="flex flex-col gap-4">
      {issued !== null ? (
        <OneTimeCode
          shown={{ what: 'invite', code: issued.code }}
          onDone={() => {
            setIssued(null)
          }}
        >
          <p className="m-0 mb-3">{deliveryNote(issued)}</p>
        </OneTimeCode>
      ) : null}
      {approving !== null ? (
        <ApproveRequest
          key={approving.id}
          request={approving}
          onCancel={() => {
            setApproving(null)
          }}
          onIssued={(made) => {
            setApproving(null)
            setIssued(made)
            list.reload()
          }}
        />
      ) : null}
      <Section title="Invite requests">
        <SegmentedControl<RequestState>
          aria-label="Show requests"
          value={state}
          options={TABS}
          onValueChange={(value) => {
            setState(value)
            setOffset(0)
            setApproving(null)
          }}
          className="mb-4"
        />
        {pending && rows !== null ? (
          <p className="m-0 mb-3" data-testid="queue-state">
            Pending: {rows.length} of {PENDING_CAP}.{' '}
            {rows.length >= PENDING_CAP
              ? 'The queue is full, so new requests are not stored until some are decided.'
              : null}
          </p>
        ) : null}
        {action.error !== null ? <Notice kind="error">{action.error}</Notice> : null}
        {list.error !== null ? <Notice kind="error">{list.error}</Notice> : null}
        {rows === null && list.error === null ? <p>Loading</p> : null}
        {rows !== null && rows.length === 0 ? <p className="text-muted">No {state} requests.</p> : null}
        {rows !== null && rows.length > 0 ? (
          <div
            className="relative overflow-x-auto"
            tabIndex={0}
            role="region"
            aria-label={`${state} requests`}
          >
            <table className="w-full border-collapse text-left text-[0.9375rem]">
              <thead className="text-muted">
                <tr>
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
                          {tierText(request)}
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
                            disabled={action.busy}
                            onClick={() => {
                              setApproving(request)
                            }}
                          >
                            Approve
                          </Button>
                          <Button
                            variant="ghost"
                            disabled={action.busy}
                            onClick={() => {
                              decline(request)
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
        ) : null}
        {pending ? null : <Pager offset={offset} nextOffset={list.next} setOffset={setOffset} />}
      </Section>
    </div>
  )
}
