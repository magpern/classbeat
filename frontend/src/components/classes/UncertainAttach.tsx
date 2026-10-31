import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError } from '../../api/client'
import {
  checkAgain,
  doItAnyway,
  listUncertainOperations,
  type Operation,
} from '../../api/jobs'
import { FORGET_WARNING } from '../account/mowlWarning'
import { Link } from '../../router/Router'
import { Notice, useAction } from '../forms'

type Outcome = 'found' | 'absent' | 'still_unsure' | 'decided'

const OUTCOMES: Record<Outcome, string> = {
  found: 'The playlist is in your MOWL music. You can design the class again now.',
  absent: 'The playlist is not in your MOWL music. You can design the class again now.',
  still_unsure: 'classbeat still cannot tell whether the playlist was added.',
  decided:
    'Understood. The next design adds the playlist to your MOWL music again, which may leave two copies there.',
}

function outcomeOf(operation: Operation): Outcome {
  if (operation.state === 'succeeded') return 'found'
  if (operation.state === 'failed') return operation.resolvedBy === 'user' ? 'decided' : 'absent'
  return 'still_unsure'
}

type Listing =
  | { status: 'loading' }
  | { status: 'error' }
  | { status: 'ready'; operations: Operation[]; scope: 'job' | 'account' }

/**
 * The undecided operations of the failed job; when its own list is empty, the account's (the
 * row may still belong to an earlier job). An empty answer from both is the honest "nothing".
 */
async function readListing(
  jobId: string,
  accept?: (operation: Operation) => boolean,
): Promise<Listing> {
  const found = await listUncertainOperations(jobId)
  const own = accept === undefined ? found : found.filter(accept)
  // A caller that names the operations it handles reads the job's own list only.
  if (accept !== undefined) return { status: 'ready', operations: own, scope: 'job' }
  if (own.length > 0) return { status: 'ready', operations: own, scope: 'job' }
  const account = await listUncertainOperations()
  return { status: 'ready', operations: account, scope: 'account' }
}

/**
 * The failure `uncertain_write` (ADR 0005 section 7): classbeat asked MOWL to add the playlist
 * and did not get a clear answer. Check again, or Do it anyway. Nothing is retried on its own.
 * `onResolved` is called whenever the user can design again: a decision was made, or nothing is
 * left to decide.
 */
export function UncertainAttach({
  jobId,
  onResolved,
  accept,
}: {
  jobId: string
  onResolved: () => void
  /** Only these operations of the job are handled here (a song job also has Spotify writes). */
  accept?: (operation: Operation) => boolean
}) {
  const [listing, setListing] = useState<Listing>({ status: 'loading' })
  const [outcome, setOutcome] = useState<Outcome | null>(null)
  const [confirming, setConfirming] = useState(false)
  const action = useAction()
  const confirmRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    // The confirm group appears below the buttons: move the focus into it for keyboard and
    // screen reader users.
    if (confirming) confirmRef.current?.focus()
  }, [confirming])

  const apply = useCallback(
    (next: Listing) => {
      setListing(next)
      // Nothing undecided is listed: the user may design again, whatever else the page says.
      if (next.status === 'ready' && next.operations.length === 0) onResolved()
    },
    [onResolved],
  )

  const load = useCallback(async () => {
    setListing({ status: 'loading' })
    try {
      apply(await readListing(jobId, accept))
    } catch {
      setListing({ status: 'error' })
    }
  }, [jobId, accept, apply])

  useEffect(() => {
    let current = true
    readListing(jobId, accept).then(
      (next) => {
        if (current) apply(next)
      },
      () => {
        if (current) setListing({ status: 'error' })
      },
    )
    return () => {
      current = false
    }
  }, [jobId, accept, apply])

  // After a decision the list is read again, so a decided row is gone; if that read fails the
  // decided row is dropped from what is shown.
  const refresh = async (decided: Operation | null) => {
    try {
      apply(await readListing(jobId, accept))
    } catch {
      if (decided === null) return
      setListing((now) =>
        now.status === 'ready'
          ? { ...now, operations: now.operations.filter((o) => o.id !== decided.id) }
          : now,
      )
    }
  }

  // The user's two actions answer 409 when the row is final or no longer undecided: look again.
  const describe = (error: unknown): string => {
    if (error instanceof ApiError && error.status === 409) {
      return 'That was already settled. The list below is up to date.'
    }
    return 'That did not work. Try again.'
  }

  const decide = (kind: 'check' | 'anyway', operation: Operation) => {
    void action.run(async () => {
      try {
        const result = await (kind === 'check' ? checkAgain(operation.id) : doItAnyway(operation.id))
        const next = outcomeOf(result)
        setOutcome(next)
        setConfirming(false)
        if (next !== 'still_unsure') onResolved()
        await refresh(next === 'still_unsure' ? null : operation)
      } catch (error) {
        if (error instanceof ApiError && error.status === 409) {
          setConfirming(false)
          await load()
        }
        throw error
      }
    }, describe)
  }

  const first = listing.status === 'ready' ? listing.operations[0] : undefined
  return (
    <div className="stack" data-testid="uncertain-attach">
      <h2>classbeat could not tell whether your playlist was added</h2>
      <p>
        To read your music, classbeat asked MOWL to add the playlist you chose to your MOWL music.
        MOWL did not answer clearly, so the playlist may or may not be there now. classbeat does
        not try again on its own, so you do not end up with two copies by accident.
      </p>
      {outcome ? (
        <Notice kind={outcome === 'still_unsure' ? 'info' : 'success'}>{OUTCOMES[outcome]}</Notice>
      ) : null}
      {action.error ? <Notice kind="error">{action.error}</Notice> : null}
      {listing.status === 'loading' ? <p>Loading</p> : null}
      {listing.status === 'error' ? (
        <>
          <Notice kind="error">What is left to decide could not be loaded.</Notice>
          <p>
            <button
              type="button"
              className="button"
              onClick={() => {
                void load()
              }}
            >
              Try again
            </button>
          </p>
        </>
      ) : null}
      {listing.status === 'ready' && first === undefined && outcome === null ? (
        <div className="notice" role="status" data-testid="no-undecided">
          <p>
            classbeat has no step left for you to decide here, so it cannot offer Check again or
            Do it anyway. If you want to be sure the playlist is not added twice, look in your
            MOWL music. To clear classbeat's record of it, disconnect MOWL and connect it again on{' '}
            <Link to="/account">your account page</Link>, then start a new design from the form.
          </p>
          <p>{FORGET_WARNING}</p>
        </div>
      ) : null}
      {listing.status === 'ready' && listing.scope === 'account' && first !== undefined ? (
        <p className="muted" data-testid="account-scope">
          This step comes from an earlier design of yours.
        </p>
      ) : null}
      {first !== undefined && !confirming ? (
        <p>
          <button
            type="button"
            className="button"
            disabled={action.busy}
            onClick={() => {
              decide('check', first)
            }}
          >
            Check again
          </button>
          <button
            type="button"
            className="button button--secondary"
            disabled={action.busy}
            onClick={() => {
              setConfirming(true)
            }}
          >
            Do it anyway
          </button>
        </p>
      ) : null}
      {first !== undefined && confirming ? (
        <div className="notice" role="group" aria-label="Confirm" tabIndex={-1} ref={confirmRef}>
          <p>
            Do it anyway means you decide the playlist was not added. The next design adds it to
            your MOWL music again. If it was added after all, you will have two copies of it there.
          </p>
          <button
            type="button"
            className="button"
            disabled={action.busy}
            onClick={() => {
              decide('anyway', first)
            }}
          >
            Yes, add it again
          </button>
          <button
            type="button"
            className="button button--secondary"
            disabled={action.busy}
            onClick={() => {
              setConfirming(false)
            }}
          >
            Cancel
          </button>
        </div>
      ) : null}
    </div>
  )
}
