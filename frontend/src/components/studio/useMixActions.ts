import { useState } from 'react'
import { ApiError } from '../../api/client'
import { getConnections } from '../../api/connections'
import { createApplyJob, createReplanJob, type MixChoicesBody } from '../../api/studioMix'
import { studioError } from '../../api/studioSongs'
import type { Timeline } from '../../api/studioOutputs'
import { useT } from '../../i18n/I18n'
import { actionKey, dropActionKey } from '../../lib/submittedInput'
import { useRouter } from '../../router/Router'
import { useAction } from '../forms'

/**
 * The two jobs the mix screen can start, each only from the user's click: **Apply these choices**
 * (`apply_mix_choices`: a new draft, no model) and **Re-plan the other songs** (a `studio_revise`
 * with the choices: a new proposal). Both go to the job's page. `refused` is the reason a job was
 * refused when it was created (for example `plan_class_too_long`, which only the job's gates know),
 * for the choices it was refused on, so the screen can offer the re-plan or Change songs for it.
 */
export function useMixActions(classId: string, timeline: Timeline, choices: MixChoicesBody) {
  const t = useT()
  const { navigate } = useRouter()
  const action = useAction()
  const key = JSON.stringify([timeline.revisionId, timeline.contentHash, choices])
  const [refusal, setRefusal] = useState<{ key: string; reason: string } | null>(null)
  const describe = (error: unknown): string => {
    if (error instanceof ApiError && error.reason !== null) setRefusal({ key, reason: error.reason })
    return studioError(error)
  }
  const base = { classId, revisionId: timeline.revisionId, contentHash: timeline.contentHash, choices }

  const apply = () => {
    const scope = `studio-apply.${key}`
    void action.run(async () => {
      const job = await createApplyJob(base, actionKey(scope))
      dropActionKey(scope)
      navigate(`/jobs/${job.id}`)
    }, describe)
  }

  const replan = () => {
    const scope = `studio-replan.${key}`
    void action.run(async () => {
      const connection = (await getConnections()).find((c) => c.provider === 'swedebeat')
      if (connection === undefined || connection.status !== 'connected') throw new ApiError(409, 'not_connected', null, undefined)
      const job = await createReplanJob(
        { ...base, connectionId: connection.id, request: t('studio.mix.replanRequest') },
        actionKey(scope),
      )
      dropActionKey(scope)
      navigate(`/jobs/${job.id}`)
    }, describe)
  }

  return {
    busy: action.busy,
    error: action.error,
    refused: refusal?.key === key ? refusal.reason : null,
    apply,
    replan,
  }
}
