import { useEffect, useState } from 'react'
import { adminError } from '../../api/admin'
import { getPlayerEvents, type PlayerEvents } from '../../api/adminJobs'
import { Notice } from '../../components/forms'
import { isReason, useMfaRequired } from './StepUp'
import { DataTable, SubTitle, when } from './Table'

/**
 * The player events classbeat-player never accepted (ADR 0034 section 2), on the Jobs page. Each
 * row shows the pairwise subject so the operator can tell the player's operator which account an
 * undelivered deletion was for (runbook `docs/runbooks/oidc-keys.md` section 4). Loaded from its
 * own route, whose every read is an admin audit row; failed rows only, never the event's `jti`.
 */
export function PlayerEventsSection() {
  const onMfaRequired = useMfaRequired()
  const [data, setData] = useState<PlayerEvents | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    getPlayerEvents(controller.signal).then(setData, (caught: unknown) => {
      if (controller.signal.aborted) return
      if (isReason(caught, 403, 'mfa_required')) onMfaRequired()
      else setError(adminError(caught))
    })
    return () => {
      controller.abort()
    }
  }, [onMfaRequired])

  return (
    <>
      <SubTitle>Undelivered player events</SubTitle>
      <p className="m-0 mb-2 text-muted">
        Account events the class player never accepted. Give the subject to the player’s operator.
        Each is removed 30 days after delivery gave up.
      </p>
      {error !== null ? <Notice kind="error">{error}</Notice> : null}
      {data === null && error === null ? <p>Loading</p> : null}
      {data !== null ? (
        <>
          <DataTable
            caption="Undelivered player events"
            head={['Event', 'Subject', 'Written', 'Gave up', 'Attempts', 'Last error']}
            empty="No undelivered player events."
            rows={data.events.map((e, index) => ({
              key: `${e.createdAt}-${String(index)}`,
              cells: [
                e.kind,
                <code key="sub" className="break-all">
                  {e.sub}
                </code>,
                when(e.createdAt),
                when(e.giveUpAt),
                e.attempts,
                e.lastError ?? 'None',
              ],
            }))}
          />
          {data.truncated ? (
            <p className="m-0 mt-2 text-muted">Only the oldest undelivered events are shown.</p>
          ) : null}
        </>
      ) : null}
    </>
  )
}
