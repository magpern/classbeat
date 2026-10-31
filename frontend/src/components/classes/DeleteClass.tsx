import { useEffect, useRef, useState } from 'react'
import { ApiError } from '../../api/client'
import { classReasonText, isStale } from '../../api/classMessages'
import { deleteClass, type ClassSummary } from '../../api/classes'
import { Notice, useAction } from '../forms'
import { Button } from '../ui/button'
import { Card } from '../ui/card'

/**
 * Delete class (ADR 0007 section 11) with its warning: the class, its versions and its
 * conversation go from classbeat; the workouts classbeat saved to MOWL stay there. While a save
 * may still land (`call_may_be_running`) it waits; with an undecided write (`uncertain_write`) it
 * asks once more, because the record of that write goes with the class. The header's ··· menu opens
 * it (`open`); Keep it closes it again.
 */
export function DeleteClass({
  summary,
  open,
  onClose,
  onDeleted,
  onReload,
}: {
  summary: ClassSummary
  open: boolean
  onClose: () => void
  onDeleted: (kept: number) => void
  onReload: () => void
}) {
  const [uncertain, setUncertain] = useState(false)
  const action = useAction()
  const card = useRef<HTMLDivElement>(null)
  // Opened from the header's menu, the card is under the Save card: on a phone it is off screen.
  // Bring it into view and move focus to it (a labelled region) so the warning is read first.
  useEffect(() => {
    if (!open) return
    card.current?.scrollIntoView({ block: 'start' })
    card.current?.focus()
  }, [open])

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

  if (!open) return null
  const kept = summary.saved.count
  return (
    <Card ref={card} tabIndex={-1} className="flex scroll-mt-4 flex-col gap-3" role="region" aria-labelledby="delete-title">
      <h2 id="delete-title" className="m-0 font-display text-xl font-bold">
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
        <Button
          
          disabled={action.busy}
          onClick={() => {
            run(uncertain)
          }}
        >
          {uncertain ? 'Delete anyway' : 'Yes, delete this class'}
        </Button>
        <Button
          variant="secondary"
          disabled={action.busy}
          onClick={() => {
            onClose()
            setUncertain(false)
            action.clear()
          }}
        >
          Keep it
        </Button>
      </p>
    </Card>
  )
}
