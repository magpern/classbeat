import { useState } from 'react'
import { ApiError } from '../../api/client'
import { checkAgain, doItAnyway, type Operation } from '../../api/jobs'
import {
  playlistOutcome,
  playlistOverwriteWarning,
  playlistUncertainText,
  playlistUncertainTitle,
  type PlaylistOutcome,
} from '../../api/songMessages'
import { useT } from '../../i18n/I18n'
import { Notice, useAction } from '../forms'
import { Button } from '../ui/button'

export const PLAYLIST_OPERATIONS: readonly string[] = ['spotify_create_playlist', 'spotify_replace_items']

function outcomeOf(operation: Operation, which: 'check' | 'anyway'): PlaylistOutcome {
  if (operation.state === 'succeeded') return 'found'
  if (operation.state === 'failed') return which === 'anyway' ? 'decided' : 'nothing'
  return 'still_unsure'
}

function waitLeft(iso: string | null): number {
  if (iso === null) return 0
  const at = Date.parse(iso)
  return Number.isNaN(at) ? 0 : Math.max(0, at - Date.now())
}

function Panel({
  operation,
  onOutcome,
}: {
  operation: Operation
  onOutcome: (outcome: PlaylistOutcome) => void
}) {
  const t = useT()
  const [outcome, setOutcome] = useState<PlaylistOutcome | null>(null)
  const [confirming, setConfirming] = useState(false)
  const action = useAction()
  const waiting = waitLeft(operation.actionableAt) > 0

  const describe = (error: unknown): string => {
    if (error instanceof ApiError && error.reason === 'call_may_be_running') {
      return t('songs.uncertain.mayStillRunning')
    }
    if (error instanceof ApiError && error.status === 409) return t('songs.uncertain.alreadySettled')
    return t('common.didNotWork')
  }

  const decide = (which: 'check' | 'anyway') => {
    void action.run(async () => {
      const result = await (which === 'check' ? checkAgain(operation.id) : doItAnyway(operation.id))
      const next = outcomeOf(result, which)
      setOutcome(next)
      setConfirming(false)
      onOutcome(next)
    }, describe)
  }

  return (
    <div className="flex flex-col gap-3" data-testid="uncertain-playlist">
      <h2 className="m-0 font-display text-xl font-bold text-text">{playlistUncertainTitle()}</h2>
      {playlistUncertainText().map((line) => (
        <p key={line} className="m-0 text-text">
          {line}
        </p>
      ))}
      {waiting ? (
        <Notice>{t('songs.uncertain.waiting')}</Notice>
      ) : null}
      {outcome !== null ? (
        <Notice kind={outcome === 'still_unsure' ? 'info' : 'success'}>{playlistOutcome(outcome)}</Notice>
      ) : null}
      {action.error ? <Notice kind="error">{action.error}</Notice> : null}
      {confirming ? (
        <div className="flex flex-col gap-3" role="group" aria-label={t('songs.uncertain.confirm')}>
          <p className="m-0 text-text">{playlistOverwriteWarning()}</p>
          <div className="flex flex-wrap gap-3">
            <Button
              disabled={action.busy || waiting}
              onClick={() => {
                decide('anyway')
              }}
            >
              {t('songs.uncertain.confirmYes')}
            </Button>
            <Button
              variant="secondary"
              disabled={action.busy}
              onClick={() => {
                setConfirming(false)
              }}
            >
              {t('common.cancel')}
            </Button>
          </div>
        </div>
      ) : (
        <div className="flex flex-wrap gap-3">
          <Button
            disabled={action.busy || waiting}
            onClick={() => {
              decide('check')
            }}
          >
            {t('songs.uncertain.checkAgain')}
          </Button>
          <Button
            variant="secondary"
            disabled={action.busy || waiting}
            onClick={() => {
              setConfirming(true)
            }}
          >
            {t('songs.uncertain.doAnyway')}
          </Button>
        </div>
      )}
    </div>
  )
}

/**
 * The undecided writes of the final playlist (ADR 0008 section 2): Check again and Do it anyway
 * as for every external write (ADR 0005 section 7). `onResolved` fires once every operation
 * shown has been decided.
 */
export function UncertainPlaylist({
  operations,
  onResolved,
}: {
  operations: Operation[]
  onResolved: () => void
}) {
  const [decided, setDecided] = useState<Set<string>>(new Set())
  return (
    <div className="flex flex-col gap-4">
      {operations.map((operation) => (
        <Panel
          key={operation.id}
          operation={operation}
          onOutcome={(outcome) => {
            if (outcome === 'still_unsure') return
            const next = new Set(decided).add(operation.id)
            setDecided(next)
            if (operations.every((o) => next.has(o.id))) onResolved()
          }}
        />
      ))}
    </div>
  )
}
