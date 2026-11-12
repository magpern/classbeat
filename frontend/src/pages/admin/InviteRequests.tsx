import { useState } from 'react'
import { adminError } from '../../api/admin'
import {
  DECLINE_MAX,
  PENDING_CAP,
  declineInviteRequest,
  declineInviteRequests,
  type InviteRequest,
  type Issued,
  type RequestState,
} from '../../api/adminInviteRequests'
import { Notice, Section, useAction } from '../../components/forms'
import { Button } from '../../components/ui/button'
import { SegmentedControl } from '../../components/ui/segmented-control'
import { ApproveRequest } from './ApproveRequest'
import { OneTimeCode } from './OneTimeCode'
import { Pager } from './Pager'
import { RequestTable } from './RequestTable'
import { useAdminWrite } from './StepUp'
import { useRequests, useSettingsFacts } from './useInviteRequests'

const TABS = [
  { value: 'pending', label: 'Pending' },
  { value: 'issued', label: 'Issued' },
  { value: 'declined', label: 'Declined' },
] as const

function deliveryNote(issued: Issued): string {
  const to = issued.request.email
  if (issued.email === 'queued') return `The code is also being emailed to ${to}.`
  if (issued.email === 'mail_off') {
    return `Mail is not set up on the server, so nothing was emailed. Send this code to ${to} yourself.`
  }
  return `Send this code to ${to} yourself.`
}

function requestsWord(n: number): string {
  return n === 1 ? '1 request' : `${String(n)} requests`
}

/**
 * Invite requests (ADR 0033): who asked for an invite, and the admin's decision. Approving makes
 * one invite code, optionally with a tier, and shows it once. Declining, one request or the
 * selected ones on this page (at most `DECLINE_MAX`), asks first: it cannot be undone. The list
 * is one page at a time; the server counts the whole pending queue against its cap (#989).
 */
export function InviteRequests() {
  const write = useAdminWrite()
  const action = useAction()
  const facts = useSettingsFacts()
  const [state, setState] = useState<RequestState>('pending')
  const [offset, setOffset] = useState(0)
  const [approving, setApproving] = useState<InviteRequest | null>(null)
  const [issued, setIssued] = useState<Issued | null>(null)
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set())
  const [confirming, setConfirming] = useState<InviteRequest[] | null>(null)
  const [note, setNote] = useState<{ kind: 'success' | 'error'; text: string } | null>(null)
  const list = useRequests(state, offset)

  const refresh = () => {
    setSelected(new Set())
    list.reload()
  }

  const decline = (requests: InviteRequest[]) => {
    setConfirming(null)
    setNote(null)
    // One request keeps its own route (a 409 says it was decided); several go in one call that
    // skips the ones decided meanwhile. The reload shows the current queue either way.
    const [only] = requests
    const task =
      requests.length === 1 && only !== undefined
        ? () => declineInviteRequest(only.id).then((r) => ({ declined: [r.id], skipped: [] }))
        : () => declineInviteRequests(requests.map((r) => r.id))
    void action
      .run(() => write(task), adminError)
      .then((done) => {
        if (done !== undefined) {
          const skipped = done.skipped.length
          setNote({
            kind: 'success',
            text:
              `Declined ${requestsWord(done.declined.length)}.` +
              (skipped > 0 ? ` ${requestsWord(skipped)} had been decided already.` : ''),
          })
        }
      })
      .then(refresh)
  }

  const rows = list.items
  const pending = state === 'pending'
  const chosen = (rows ?? []).filter((r) => selected.has(r.id)).slice(0, DECLINE_MAX)
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
          plans={facts.plans}
          plansError={facts.error}
          mailConfigured={facts.mailConfigured}
          onCancel={() => {
            setApproving(null)
          }}
          onStale={(words) => {
            // Decided meanwhile (#994): close the form, say so here and show the current queue.
            setApproving(null)
            setNote({ kind: 'error', text: words })
            refresh()
          }}
          onIssued={(made) => {
            setApproving(null)
            setIssued(made)
            refresh()
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
            setConfirming(null)
            setSelected(new Set())
          }}
          className="mb-4"
        />
        {pending && list.pending !== null ? (
          <p className="m-0 mb-3" data-testid="queue-state">
            Pending: {list.pending} of {PENDING_CAP}.{' '}
            {list.pending >= PENDING_CAP
              ? 'The queue is full, so new requests are not stored until some are decided.'
              : null}
          </p>
        ) : null}
        {note !== null ? <Notice kind={note.kind}>{note.text}</Notice> : null}
        {action.error !== null ? <Notice kind="error">{action.error}</Notice> : null}
        {list.error !== null ? <Notice kind="error">{list.error}</Notice> : null}
        {confirming !== null ? (
          <div className="notice mb-3" role="group" aria-label="Confirm the decline">
            <p className="m-0 mb-2">
              {confirming.length === 1
                ? `Decline the request from ${confirming[0]?.email ?? ''}?`
                : `Decline ${requestsWord(confirming.length)}?`}{' '}
              This cannot be undone.
            </p>
            <div className="flex flex-wrap gap-2">
              <Button
                disabled={action.busy}
                onClick={() => {
                  decline(confirming)
                }}
              >
                Yes, decline
              </Button>
              <Button
                variant="secondary"
                onClick={() => {
                  setConfirming(null)
                }}
              >
                Keep
              </Button>
            </div>
          </div>
        ) : null}
        {pending && chosen.length > 0 && confirming === null ? (
          <div className="mb-3">
            <Button
              variant="secondary"
              disabled={action.busy}
              onClick={() => {
                setConfirming(chosen)
              }}
            >
              Decline selected ({chosen.length})
            </Button>
          </div>
        ) : null}
        {rows === null && list.error === null ? <p>Loading</p> : null}
        {rows !== null && rows.length === 0 ? <p className="text-muted">No {state} requests.</p> : null}
        {rows !== null && rows.length > 0 ? (
          <RequestTable
            label={`${state} requests`}
            rows={rows}
            pending={pending}
            plans={facts.plans}
            busy={action.busy}
            selected={selected}
            onSelect={(ids, on) => {
              setSelected((before) => {
                const after = new Set(before)
                for (const id of ids) {
                  if (on) after.add(id)
                  else after.delete(id)
                }
                return after
              })
            }}
            onApprove={(request) => {
              setConfirming(null)
              setNote(null)
              setApproving(request)
            }}
            onDecline={(request) => {
              setConfirming([request])
            }}
          />
        ) : null}
        <Pager
          offset={offset}
          nextOffset={list.next}
          setOffset={(n) => {
            setSelected(new Set())
            setConfirming(null) // a confirmation names requests of the page it was asked on (#994)
            setOffset(n)
          }}
        />
      </Section>
    </div>
  )
}
