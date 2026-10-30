import { useState } from 'react'
import { ApiError } from '../../api/client'
import { classReasonText, isStale } from '../../api/classMessages'
import { deleteClass, type ClassSummary } from '../../api/classes'
import { Notice, useAction } from '../forms'

/**
 * Delete class (ADR 0007 section 11) with its warning: the class, its versions and its
 * conversation go from classbeat; the workouts classbeat saved to MOWL stay there. While a save
 * may still land (`call_may_be_running`) it waits; with an undecided write (`uncertain_write`) it
 * asks once more, because the record of that write goes with the class.
 */
export function DeleteClass({
  summary,
  onDeleted,
  onReload,
}: {
  summary: ClassSummary
  onDeleted: (kept: number) => void
  onReload: () => void
}) {
  const [open, setOpen] = useState(false)
  const [uncertain, setUncertain] = useState(false)
  const action = useAction()

  const run = (confirmUncertain: boolean) => {
    void action
      .run(
        async () => deleteClass(summary.id, confirmUncertain),
        (error) => {
          if (error instanceof ApiError && error.reason === 'uncertain_write') {
            setUncertain(true)
            return 'classbeat is not sure whether a save of this class to MOWL went through. Deleting the class now removes the only record of it.'
          }
          if (isStale(error)) onReload()
          return classReasonText(error)
        },
      )
      .then((done) => {
        if (done !== undefined) onDeleted(done.mowlWorkoutsKept)
      })
  }

  if (!open) {
    return (
      <section className="panel" aria-labelledby="delete-title">
        <h2 id="delete-title" className="panel__title">
          Delete class
        </h2>
        <button
          type="button"
          className="button button--secondary"
          onClick={() => {
            setOpen(true)
          }}
        >
          Delete class
        </button>
      </section>
    )
  }
  const kept = summary.saved.count
  return (
    <section className="panel" aria-labelledby="delete-title">
      <h2 id="delete-title" className="panel__title">
        Delete class
      </h2>
      <Notice kind="error">
        This deletes the class, all its versions and your conversation from classbeat. It cannot be
        undone.{' '}
        {kept > 0
          ? `The ${String(kept)} workout${kept === 1 ? '' : 's'} classbeat saved to your MOWL account stay${kept === 1 ? 's' : ''} there; delete ${kept === 1 ? 'it' : 'them'} in MOWL.`
          : 'Any workout classbeat saved to your MOWL account stays there; delete it in MOWL.'}
      </Notice>
      {action.error ? <Notice kind="error">{action.error}</Notice> : null}
      <p>
        <button
          type="button"
          className="button"
          disabled={action.busy}
          onClick={() => {
            run(uncertain)
          }}
        >
          {uncertain ? 'Delete anyway' : 'Yes, delete this class'}
        </button>
        <button
          type="button"
          className="button button--secondary"
          disabled={action.busy}
          onClick={() => {
            setOpen(false)
            setUncertain(false)
            action.clear()
          }}
        >
          Keep it
        </button>
      </p>
    </section>
  )
}
