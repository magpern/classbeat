import { useCallback, useEffect, useState } from 'react'
import { ApiError } from '../../api/client'
import {
  checkAgain,
  doItAnyway,
  listUncertainOperations,
  type Operation,
} from '../../api/jobs'
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

/**
 * The failure `uncertain_write` (ADR 0005 section 7): classbeat asked MOWL to add the playlist
 * and did not get a clear answer. Check again, or Do it anyway. Nothing is retried on its own.
 * `onResolved` is called once the user can design again.
 */
export function UncertainAttach({
  jobId,
  onResolved,
}: {
  jobId: string
  onResolved: () => void
}) {
  const [operations, setOperations] = useState<Operation[] | null>(null)
  const [outcome, setOutcome] = useState<Outcome | null>(null)
  const [confirming, setConfirming] = useState(false)
  const action = useAction()

  const load = useCallback(
    async () => {
      const found = await listUncertainOperations(jobId)
      setOperations(found)
      return found
    },
    [jobId],
  )

  useEffect(() => {
    let current = true
    listUncertainOperations(jobId).then(
      (found) => {
        if (!current) return
        setOperations(found)
        // Nothing undecided is listed (a stale row is hidden, #117): the user may design again.
        if (found.length === 0) onResolved()
      },
      () => {
        if (current) setOperations([])
      },
    )
    return () => {
      current = false
    }
  }, [jobId, onResolved])

  // The user's two actions answer 409 when the row is final or no longer undecided: look again.
  const describe = (error: unknown): string => {
    if (error instanceof ApiError && error.status === 409) {
      return 'That was already settled. The list below is up to date.'
    }
    return 'That did not work. Try again.'
  }

  const decide = (kind: 'check' | 'anyway', operation: Operation) => {
    void action
      .run(async () => {
        try {
          const result = await (kind === 'check' ? checkAgain(operation.id) : doItAnyway(operation.id))
          const next = outcomeOf(result)
          setOutcome(next)
          setConfirming(false)
          if (next !== 'still_unsure') onResolved()
        } catch (error) {
          if (error instanceof ApiError && error.status === 409) await load().catch(() => undefined)
          throw error
        }
      }, describe)
  }

  const first = operations?.[0]
  return (
    <div className="stack" data-testid="uncertain-attach">
      <h2>classbeat could not tell whether your playlist was added</h2>
      <p>
        To read your music, classbeat asked MOWL to add the playlist you chose to your MOWL music.
        MOWL did not answer clearly, so the playlist may or may not be there now. classbeat does
        not try again on its own, so you do not end up with two copies by accident.
      </p>
      {outcome ? <Notice kind={outcome === 'still_unsure' ? 'info' : 'success'}>{OUTCOMES[outcome]}</Notice> : null}
      {action.error ? <Notice kind="error">{action.error}</Notice> : null}
      {operations === null ? <p>Loading</p> : null}
      {operations !== null && first === undefined && outcome === null ? (
        <p className="muted" data-testid="no-undecided">
          There is nothing to decide here right now. If you design again, classbeat checks the
          playlist once more.
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
        <div className="notice" role="group" aria-label="Confirm">
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
