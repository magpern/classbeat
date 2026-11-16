import { useEffect, useState } from 'react'
import type { ClassSummary } from '../../api/classes'
import { getJob } from '../../api/jobs'
import { startStudioDesign } from '../../api/studioOutputs'
import { studioError } from '../../api/studioSongs'
import { useT } from '../../i18n/I18n'
import { actionKey, dropActionKey } from '../../lib/submittedInput'
import { useRouter } from '../../router/Router'
import { Notice, useAction } from '../forms'
import { Button } from '../ui/button'
import { Card, CardTitle } from '../ui/card'
import { recallStudioDesign, rememberStudioDesign } from './studioAction'

/**
 * A studio class whose song list is approved but has no draft: the design never started (its
 * start failed, or the page was closed in between). The page has already read the list's job and
 * mounts this only for a `studio_songs` list. The same per-version key as the approval
 * page is used, so a start that did go through is returned, not made twice.
 */
export function StudioStartDesign({ summary }: { summary: ClassSummary }) {
  const t = useT()
  const { navigate } = useRouter()
  const { jobId, revisionId, state } = summary.songs
  const [ranFailed, setRanFailed] = useState<{ jobId: string; ranAndFailed: boolean } | null>(null)
  const action = useAction()
  const approved = state === 'approved' && jobId !== null && revisionId !== null

  useEffect(() => {
    if (!approved) return
    const abort = new AbortController()
    const ran = recallStudioDesign(summary.id)
    const failed =
      ran === null
        ? Promise.resolve(false)
        : getJob(ran, abort.signal).then(
            (design) => design.kind === 'studio_design' && design.state === 'failed',
            () => false,
          )
    failed.then(
      (ranAndFailed) => {
        if (!abort.signal.aborted) setRanFailed({ jobId, ranAndFailed })
      },
      () => undefined,
    )
    return () => {
      abort.abort()
    }
  }, [approved, jobId, summary.id])

  if (!approved || ranFailed?.jobId !== jobId) return null
  const scope = `studio-design.${revisionId}`
  const start = () => {
    void action.run(async () => {
      const design = await startStudioDesign(summary.id, revisionId, actionKey(scope))
      dropActionKey(scope)
      rememberStudioDesign(summary.id, design.id)
      navigate(`/jobs/${design.id}`)
    }, studioError)
  }
  return (
    <Card className="flex flex-col gap-3" data-testid="studio-start-design">
      <CardTitle>{ranFailed.ranAndFailed ? t('studio.designRunFailed') : t('studio.designFailed')}</CardTitle>
      {action.error ? <Notice kind="error">{action.error}</Notice> : null}
      <div>
        <Button disabled={summary.openJob !== null || action.busy} onClick={start}>
          {t('studio.designRetry')}
        </Button>
      </div>
    </Card>
  )
}
