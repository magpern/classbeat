import { useEffect, useRef, useState } from 'react'
import { ApiError } from '../../api/client'
import { classReasonText, isStale } from '../../api/classMessages'
import { deleteClass, type ClassSummary } from '../../api/classes'
import { useT } from '../../i18n/I18n'
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
  const t = useT()
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
            return t('class.delete.uncertain')
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
        {t('class.delete.title')}
      </h2>
      <Notice kind="error">
        {t('class.delete.warning')}{' '}
        {kept > 0 ? t('class.delete.kept', { count: kept }) : t('class.delete.keptNone')}
      </Notice>
      {action.error ? <Notice kind="error">{action.error}</Notice> : null}
      <p>
        <Button
          
          disabled={action.busy}
          onClick={() => {
            run(uncertain)
          }}
        >
          {uncertain ? t('class.delete.anyway') : t('class.delete.confirm')}
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
          {t('class.delete.keep')}
        </Button>
      </p>
    </Card>
  )
}
