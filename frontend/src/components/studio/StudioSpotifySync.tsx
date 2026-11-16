import { useEffect, useState } from 'react'
import { ApiError } from '../../api/client'
import { getSpotifySync, syncAgain, type SyncStatus, type SyncTarget } from '../../api/spotifySync'
import type { CatalogueKey } from '../../i18n/en'
import { useT } from '../../i18n/I18n'
import { Notice, useAction } from '../forms'
import { Button } from '../ui/button'

// How often a pending sync is read again (a sync is a few Spotify requests).
const POLL_MS = 3000

const DETAILS: Partial<Record<string, CatalogueKey>> = {
  superseded: 'studio.sync.detail.superseded',
  not_connected: 'studio.sync.detail.not_connected',
  needs_reconnect: 'studio.sync.detail.not_connected',
  account_changed: 'studio.sync.detail.not_connected',
  consent_missing: 'studio.sync.detail.consent_missing',
  pool_missing: 'studio.sync.detail.pool_missing',
  rate_limited: 'studio.sync.detail.rate_limited',
}

/**
 * One revision's Spotify sync state (ADR 0036 section 9): the working playlist following a studio
 * song list, or the final playlist of an approved draft. `synced`, `pending` (read again until it
 * settles) or `failed` with "Sync again". Nothing is shown before the revision has a state. A
 * failure here never stops the SwedeBeat outputs or the approval of the list.
 */
export function StudioSpotifySync({ classId, revisionId, target }: { classId: string; revisionId: string; target: SyncTarget }) {
  const t = useT()
  const [status, setStatus] = useState<{ key: string; value: SyncStatus | null } | null>(null)
  const [round, setRound] = useState(0)
  const again = useAction()
  const key = `${classId}/${revisionId}/${target}`
  const shown = status?.key === key ? status.value : null

  useEffect(() => {
    const abort = new AbortController()
    getSpotifySync(classId, revisionId, abort.signal).then(
      (view) => {
        if (!abort.signal.aborted) setStatus({ key, value: view === null ? null : view[target] })
      },
      () => {
        // A failed read keeps what was shown; the next round reads again.
      },
    )
    return () => {
      abort.abort()
    }
  }, [classId, revisionId, target, key, round])

  const pending = shown?.state === 'pending'
  useEffect(() => {
    if (!pending) return
    const timer = window.setTimeout(() => { setRound((n) => n + 1) }, POLL_MS)
    return () => {
      window.clearTimeout(timer)
    }
  }, [pending, round])

  if (shown === null) return null

  const press = () => {
    void again.run(
      async () => {
        try {
          await syncAgain(classId, revisionId, target)
        } catch (error) {
          // Already started (a second press, another tab): read the state as it is.
          if (!(error instanceof ApiError && error.reason === 'not_failed')) throw error
        }
        setStatus({ key, value: { state: 'pending', detail: null } })
        setRound((n) => n + 1)
      },
      () => t('studio.sync.againFailed'),
    )
  }

  const detail = shown.detail === null ? null : (DETAILS[shown.detail] ?? 'studio.sync.detail.other')
  return (
    <div className="flex flex-col gap-2" data-testid={`studio-sync-${target}`}>
      <p className="m-0 text-text">
        <span className="font-medium">{t(target === 'final' ? 'studio.sync.final' : 'studio.sync.working_copy')}</span>
        {': '}
        <span data-testid={`studio-sync-${target}-state`}>{t(`studio.sync.state.${shown.state}`)}</span>
      </p>
      {shown.state === 'failed' ? (
        <>
          {detail !== null ? <p className="m-0 text-muted">{t(detail)}</p> : null}
          <p className="m-0 text-muted">{t('studio.sync.notBlocking')}</p>
          <div>
            <Button variant="secondary" disabled={again.busy} onClick={press}>
              {t('studio.sync.again')}
            </Button>
          </div>
        </>
      ) : null}
      {again.error ? <Notice kind="error">{again.error}</Notice> : null}
    </div>
  )
}
