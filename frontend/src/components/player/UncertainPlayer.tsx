import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError } from '../../api/client'
import { checkAgain, doItAnyway, listUncertainOperations, type Operation } from '../../api/jobs'
import { playerOutcomeText, type PlayerOutcome } from '../../api/playerMessages'
import { PLAYER_REMOVE_OPERATION } from '../../api/playerSend'
import { useT } from '../../i18n/I18n'
import { CheckNeeded } from '../classes/CheckNeeded'
import { Notice, useAction } from '../forms'
import { Button } from '../ui/button'

/**
 * The backend records `resolved_by = 'user'` for both buttons, so a failed row alone cannot tell
 * the user's own decision (Do it anyway) from a check that proved nothing arrived: the button
 * pressed decides.
 */
function outcomeOf(operation: Operation, which: 'check' | 'anyway'): PlayerOutcome {
  if (operation.state === 'succeeded') return 'found'
  if (operation.state === 'failed') return which === 'anyway' ? 'decided' : 'nothing'
  return 'still_unsure'
}

function Panel({
  operation,
  onDecided,
  onChanged,
}: {
  operation: Operation
  onDecided: () => void
  onChanged: () => void
}) {
  const t = useT()
  const which = operation.kind === PLAYER_REMOVE_OPERATION ? 'remove' : 'send'
  const [outcome, setOutcome] = useState<PlayerOutcome | null>(null)
  const [confirming, setConfirming] = useState(false)
  const action = useAction()
  const confirmRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (confirming) confirmRef.current?.focus()
  }, [confirming])

  const decide = (button: 'check' | 'anyway') => {
    void action.run(
      async () => {
        try {
          const result = await (button === 'check' ? checkAgain(operation.id) : doItAnyway(operation.id))
          const next = outcomeOf(result, button)
          setOutcome(next)
          setConfirming(false)
          if (next !== 'still_unsure') onDecided()
        } catch (error) {
          if (error instanceof ApiError && error.status === 409) {
            setConfirming(false)
            onChanged()
          }
          throw error
        }
      },
      (error) =>
        error instanceof ApiError && error.status === 409 ? t('class.write.settled') : t('error.generic'),
    )
  }

  return (
    <div className="flex flex-col gap-3" data-testid="uncertain-player" data-kind={which}>
      <h3>{t(`player.write.${which}.title`)}</h3>
      <p>{t(`player.write.${which}.text`)}</p>
      {outcome ? (
        <Notice kind={outcome === 'still_unsure' ? 'info' : 'success'}>
          {playerOutcomeText(operation.kind, outcome)}
        </Notice>
      ) : null}
      {action.error ? <Notice kind="error">{action.error}</Notice> : null}
      {!confirming ? (
        <p>
          <Button
            disabled={action.busy}
            onClick={() => {
              decide('check')
            }}
          >
            {t('class.checkAgain')}
          </Button>
          <Button
            variant="secondary"
            disabled={action.busy}
            onClick={() => {
              setConfirming(true)
            }}
          >
            {t('class.doItAnyway')}
          </Button>
        </p>
      ) : (
        <div className="notice" role="group" aria-label={t('class.confirm')} tabIndex={-1} ref={confirmRef}>
          <p>{t(`player.write.confirm.${which}`)}</p>
          <Button
            disabled={action.busy}
            onClick={() => {
              decide('anyway')
            }}
          >
            {t(`player.write.yes.${which}`)}
          </Button>
          <Button
            variant="secondary"
            disabled={action.busy}
            onClick={() => {
              setConfirming(false)
            }}
          >
            {t('class.cancel')}
          </Button>
        </div>
      )}
    </div>
  )
}

/**
 * The player's undecided operations that `keep` selects (ADR 0034 sections 5 and 6): a send of one
 * class, or the removal a class's deletion made. Nothing is sent again on its own: Check again asks
 * the player, and Do it anyway only says it did not arrive. `version` changes whenever the list must
 * be read again.
 */
export function UncertainPlayer({
  keep,
  version,
  onDecided,
  onChanged,
}: {
  keep: (operation: Operation) => boolean
  version: number
  /** A decision was made or a check found the truth. */
  onDecided: () => void
  onChanged: () => void
}) {
  const t = useT()
  const [operations, setOperations] = useState<Operation[] | null>(null)
  const [failed, setFailed] = useState(false)
  const [round, setRound] = useState(0)
  const reread = useCallback(() => {
    setRound((n) => n + 1)
    onChanged()
  }, [onChanged])
  const keepRef = useRef(keep)
  useEffect(() => {
    keepRef.current = keep
  })

  useEffect(() => {
    let current = true
    listUncertainOperations().then(
      (all) => {
        if (!current) return
        setFailed(false)
        setOperations(all.filter(keepRef.current))
      },
      () => {
        if (current) setFailed(true)
      },
    )
    return () => {
      current = false
    }
  }, [version, round])

  if (failed && operations === null) return <Notice kind="error">{t('class.write.loadFailed')}</Notice>
  if (operations === null || operations.length === 0) return null
  return (
    <CheckNeeded aria-labelledby="undecided-player-title" data-testid="undecided-player">
      <h2 id="undecided-player-title" className="m-0 font-display text-xl font-bold">
        {t('player.write.heading')}
      </h2>
      {operations.map((operation) => (
        <Panel
          key={operation.id}
          operation={operation}
          onDecided={() => {
            onDecided()
            setRound((n) => n + 1)
          }}
          onChanged={reread}
        />
      ))}
    </CheckNeeded>
  )
}
