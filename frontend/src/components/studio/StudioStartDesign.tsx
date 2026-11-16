import { useEffect, useState } from 'react'
import type { ClassSummary } from '../../api/classes'
import { getJob } from '../../api/jobs'
import { startStudioDesign } from '../../api/studioOutputs'
import { STUDIO_KIND, studioError } from '../../api/studioSongs'
import { useT } from '../../i18n/I18n'
import { actionKey, dropActionKey } from '../../lib/submittedInput'
import { useRouter } from '../../router/Router'
import { Notice, useAction } from '../forms'
import { Button } from '../ui/button'
import { Card, CardTitle } from '../ui/card'

/**
 * A studio class whose song list is approved but has no draft: the design never started (its
 * start failed, or the page was closed in between). The same per-version key as the approval
 * page is used, so a start that did go through is returned, not made twice.
 */
export function StudioStartDesign({ summary }: { summary: ClassSummary }) {
  const t = useT()
  const { navigate } = useRouter()
  const { jobId, revisionId, state } = summary.songs
  const [studio, setStudio] = useState<{ jobId: string; studio: boolean } | null>(null)
  const action = useAction()
  const approved = state === 'approved' && jobId !== null && revisionId !== null

  useEffect(() => {
    if (!approved) return
    const abort = new AbortController()
    getJob(jobId, abort.signal).then(
      (job) => {
        if (!abort.signal.aborted) setStudio({ jobId, studio: job.kind === STUDIO_KIND })
      },
      () => {
        if (!abort.signal.aborted) setStudio({ jobId, studio: false })
      },
    )
    return () => {
      abort.abort()
    }
  }, [approved, jobId])

  if (!approved || studio?.jobId !== jobId || !studio.studio) return null
  const scope = `studio-design.${revisionId}`
  const start = () => {
    void action.run(async () => {
      const design = await startStudioDesign(summary.id, revisionId, actionKey(scope))
      dropActionKey(scope)
      navigate(`/jobs/${design.id}`)
    }, studioError)
  }
  return (
    <Card className="flex flex-col gap-3" data-testid="studio-start-design">
      <CardTitle>{t('studio.designFailed')}</CardTitle>
      {action.error ? <Notice kind="error">{action.error}</Notice> : null}
      <div>
        <Button disabled={summary.openJob !== null || action.busy} onClick={start}>
          {t('studio.designRetry')}
        </Button>
      </div>
    </Card>
  )
}
