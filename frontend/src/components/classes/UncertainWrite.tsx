import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError } from '../../api/client'
import { checkAgain, doItAnyway, listUncertainOperations, type Operation } from '../../api/jobs'
import {
  duplicateWarning,
  outcomeText,
  overwriteWarning,
  uncertainText,
  type WriteKind,
  type WriteOutcome,
} from '../../api/saveMessages'
import { SAVE_OPERATION, UPDATE_OPERATION } from '../../api/saving'
import { Button } from '../ui/button'
import { CheckNeeded } from './CheckNeeded'
import { Notice, useAction } from '../forms'
import { time } from '../../i18n/format'
import { useT } from '../../i18n/I18n'

function kindOf(operation: Operation): WriteKind | null {
  if (operation.kind === SAVE_OPERATION) return 'save'
  if (operation.kind === UPDATE_OPERATION) return 'update'
  return null
}

/**
 * The backend records `resolved_by = 'user'` for both buttons, so a failed row alone cannot tell
 * the user's own decision (Do it anyway) from a check that proved nothing was written: the
 * button pressed decides.
 */
function outcomeOf(operation: Operation, which: 'check' | 'anyway'): WriteOutcome {
  if (operation.state === 'succeeded') return 'found'
  if (operation.state === 'failed') return which === 'anyway' ? 'decided' : 'nothing'
  return 'still_unsure'
}

/** Milliseconds until `iso`, or 0 when it is past or not a time. */
function waitMs(iso: string | null, now: number): number {
  if (iso === null) return 0
  const at = Date.parse(iso)
  return Number.isNaN(at) ? 0 : Math.max(0, at - now)
}

const MAX_TIMER_MS = 2 ** 31 - 1

/** The current time, ticking once when `until` passes, so a waiting panel opens by itself. */
function useNow(until: string | null): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const left = waitMs(until, Date.now())
    if (left === 0) return
    const timer = setTimeout(() => {
      setNow(Date.now())
    }, Math.min(left + 50, MAX_TIMER_MS))
    return () => {
      clearTimeout(timer)
    }
  }, [until, now])
  return now
}

function clockText(iso: string): string {
  return time(iso)
}

function Panel({
  operation,
  kind,
  onDecided,
  onChanged,
}: {
  operation: Operation
  kind: WriteKind
  onDecided: (kind: WriteKind, outcome: WriteOutcome) => void
  onChanged: () => void
}) {
  const t = useT()
  const [outcome, setOutcome] = useState<WriteOutcome | null>(null)
  const [confirming, setConfirming] = useState(false)
  const action = useAction()
  const confirmRef = useRef<HTMLDivElement>(null)
  const now = useNow(operation.actionableAt)
  const waiting = waitMs(operation.actionableAt, now) > 0

  useEffect(() => {
    if (confirming) confirmRef.current?.focus()
  }, [confirming])

  const describe = (error: unknown): string => {
    if (error instanceof ApiError && error.reason === 'call_may_be_running') {
      return t('class.write.running')
    }
    if (error instanceof ApiError && error.status === 409) {
      return t('class.write.settled')
    }
    return t('error.generic')
  }

  const decide = (which: 'check' | 'anyway') => {
    void action.run(async () => {
      try {
        const result = await (which === 'check' ? checkAgain(operation.id) : doItAnyway(operation.id))
        const next = outcomeOf(result, which)
        setOutcome(next)
        setConfirming(false)
        onDecided(kind, next)
      } catch (error) {
        if (error instanceof ApiError && error.status === 409) {
          setConfirming(false)
          onChanged()
        }
        throw error
      }
    }, describe)
  }

  return (
    <div className="flex flex-col gap-3" data-testid="uncertain-write" data-kind={kind}>
      <h3>
        {t(kind === 'save' ? 'class.write.title.save' : 'class.write.title.update')}
      </h3>
      {uncertainText(kind).map((line) => (
        <p key={line}>{line}</p>
      ))}
      {waiting && operation.actionableAt !== null ? (
        <Notice>
          {t('class.write.waiting', { time: clockText(operation.actionableAt) })}
        </Notice>
      ) : null}
      {outcome ? (
        <Notice kind={outcome === 'still_unsure' ? 'info' : 'success'}>{outcomeText(kind, outcome)}</Notice>
      ) : null}
      {action.error ? <Notice kind="error">{action.error}</Notice> : null}
      {!confirming ? (
        <p>
          <Button
            
            disabled={action.busy || waiting}
            onClick={() => {
              decide('check')
            }}
          >
            {t('class.checkAgain')}
          </Button>
          <Button
            variant="secondary"
            disabled={action.busy || waiting}
            onClick={() => {
              setConfirming(true)
            }}
          >
            {t('class.doItAnyway')}
          </Button>
        </p>
      ) : (
        <div className="notice" role="group" aria-label={t('class.confirm')} tabIndex={-1} ref={confirmRef}>
          <p>{kind === 'save' ? duplicateWarning() : overwriteWarning()}</p>
          <Button
            
            disabled={action.busy || waiting}
            onClick={() => {
              decide('anyway')
            }}
          >
            {kind === 'save' ? t('class.write.notSaved') : t('class.write.notUpdated')}
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
 * The undecided saves and updates of one class (ADR 0007 section 8), from the account's list of
 * undecided operations filtered to this class. A write left by a job the reaper ended shows here
 * as the same panel, since the server marks it `uncertain` before listing. Nothing is retried on
 * its own. `version` changes whenever the list must be read again (a refused Save, say).
 */
export function UncertainWrite({
  classId,
  version,
  onDecided,
  onChanged,
}: {
  classId: string
  version: number
  /** A decision was made or a check found the truth: the buttons may offer the new job. */
  onDecided: (kind: WriteKind, outcome: WriteOutcome) => void
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

  useEffect(() => {
    let current = true
    listUncertainOperations().then(
      (all) => {
        if (!current) return
        setFailed(false)
        setOperations(all.filter((o) => o.classId === classId && kindOf(o) !== null))
      },
      () => {
        if (current) setFailed(true)
      },
    )
    return () => {
      current = false
    }
  }, [classId, version, round])

  if (failed && operations === null) {
    return <Notice kind="error">{t('class.write.loadFailed')}</Notice>
  }
  if (operations === null || operations.length === 0) return null
  return (
    <CheckNeeded aria-labelledby="undecided-title" data-testid="undecided">
      <h2 id="undecided-title" className="m-0 font-display text-xl font-bold">
        {t('class.write.heading')}
      </h2>
      {operations.map((operation) => (
        <Panel
          key={operation.id}
          operation={operation}
          kind={kindOf(operation)!}
          onDecided={(kind, outcome) => {
            onDecided(kind, outcome)
            if (outcome !== 'still_unsure') setRound((n) => n + 1)
          }}
          onChanged={reread}
        />
      ))}
    </CheckNeeded>
  )
}
