import { useState } from 'react'
import { studioError } from '../../api/studioSongs'
import {
  chooseOutputs,
  DEFAULT_OUTPUTS,
  OUTPUTS,
  toggleOutput,
  type Output,
  type Timeline,
} from '../../api/studioOutputs'
import { useT } from '../../i18n/I18n'
import { actionKey, dropActionKey } from '../../lib/submittedInput'
import { forgetOutputLimit } from './studioAction'
import { useRouter } from '../../router/Router'
import { Notice, useAction } from '../forms'
import { Button } from '../ui/button'

/**
 * The output choice and its consent (ADR 0036 section 6): the defaults of the matrix, the facts
 * the user accepts (SwedeBeat has no delete; a published playlist shows their name and email; what
 * a merged mix shows others is unconfirmed), and the approval of this draft for those outputs.
 *
 * `enabled` is the outputs the server runs for the user (`GET /outputs`), so only those are
 * offered and the defaults fall back to what is on; null (the list could not be read) offers all
 * four and a switched-off one is refused in words. A draft whose mix repeats a song
 * (`timeline.hasRepeats`) is mix-only (ADR 0036 amendment A8): the SwedeBeat playlist is shown off
 * and disabled with the reason, and the server refuses it too. The per-output states and their
 * actions are `StudioOutputStates`.
 */
export function StudioOutputs({
  classId,
  timeline,
  disabled,
  enabled = null,
  limitReached = false,
}: {
  classId: string
  timeline: Timeline
  /** Another job of the class is open. */
  disabled: boolean
  enabled?: readonly Output[] | null
  /** The class's last approval stopped at the limit: a notice only, Approve stays on. */
  limitReached?: boolean
}) {
  const t = useT()
  const { navigate } = useRouter()
  const offered = OUTPUTS.filter((o) => enabled === null || enabled.includes(o))
  const mixOnly = timeline.hasRepeats
  const allowed = (o: Output) => offered.includes(o) && !(mixOnly && o === 'swedebeat_playlist')
  const [picked, setChosen] = useState<Output[]>(() => DEFAULT_OUTPUTS.filter(allowed))
  // An output that went off after the page loaded is dropped from the choice (and MOWL goes with a mix that is off),
  // and so is the SwedeBeat playlist of a draft that repeats a song.
  const chosen = picked.filter((o) => allowed(o) && (o !== 'mowl' || offered.includes('swedebeat_mix')))
  const action = useAction()
  const has = (output: Output) => chosen.includes(output)
  const publishes = has('swedebeat_playlist') || has('swedebeat_mix')

  const approve = () => {
    const scope = `studio-outputs.${timeline.revisionId}.${timeline.contentHash}.${chosen.join('+')}`
    void action.run(async () => {
      const job = await chooseOutputs(
        classId,
        { revisionId: timeline.revisionId, contentHash: timeline.contentHash, outputs: chosen },
        actionKey(scope),
      )
      dropActionKey(scope)
      forgetOutputLimit(classId)
      navigate(`/jobs/${job.id}`)
    }, studioError)
  }

  return (
    <section className="flex flex-col gap-3" aria-labelledby="studio-out-title" data-testid="studio-outputs">
      <h3 id="studio-out-title" className="m-0 font-display text-lg font-bold">
        {t('studio.out.title')}
      </h3>
      <p className="m-0 text-muted">{t('studio.out.intro')}</p>
      <ul className="m-0 flex list-none flex-col gap-2 p-0">
        {offered.map((output) => {
          const locked = output === 'swedebeat_mix' && has('mowl')
          const repeats = mixOnly && output === 'swedebeat_playlist'
          return (
            <li key={output} className="rounded-md border border-line bg-surface p-3">
              <label className="flex min-h-11 cursor-pointer items-start gap-3">
                <input
                  type="checkbox"
                  className="mt-1"
                  checked={has(output)}
                  disabled={locked || repeats || action.busy || disabled}
                  onChange={(event) => {
                    setChosen(toggleOutput(chosen, output, event.target.checked))
                  }}
                />
                <span>
                  <span className="block font-medium text-text">{t(`studio.out.${output}`)}</span>
                  <span className="block text-[0.9375rem] text-muted">{t(`studio.out.${output}.desc`)}</span>
                  {locked ? <span className="block text-[0.9375rem] text-muted">{t('studio.out.mowlNeedsMix')}</span> : null}
                  {repeats ? (
                    <span className="block text-[0.9375rem] text-muted" data-testid="studio-out-mix-only">
                      {t('studio.out.playlistHasRepeats')}
                    </span>
                  ) : null}
                </span>
              </label>
            </li>
          )
        })}
      </ul>
      {chosen.length === 0 ? (
        <Notice>{t(offered.length === 0 ? 'studio.out.noneEnabled' : 'studio.out.none')}</Notice>
      ) : (
        <div className="flex flex-col gap-1" data-testid="studio-consent">
          <p className="m-0 font-medium text-text">{t('studio.consent.title')}</p>
          <p className="m-0 text-text">
            {t('studio.consent.chosen', { outputs: chosen.map((o) => t(`studio.out.${o}`)).join(', ') })}
          </p>
          {publishes ? <p className="m-0 text-muted">{t('studio.consent.noDelete')}</p> : null}
          {has('swedebeat_playlist') ? <p className="m-0 text-muted">{t('studio.consent.publicPlaylist')}</p> : null}
          {has('swedebeat_mix') ? <p className="m-0 text-muted">{t('studio.consent.mix')}</p> : null}
          {mixOnly ? <p className="m-0 text-muted">{t('studio.consent.repeats')}</p> : null}
        </div>
      )}
      {limitReached ? <Notice>{t('studio.os.limitReached')}</Notice> : null}
      {action.error ? <Notice kind="error">{action.error}</Notice> : null}
      <div>
        <Button disabled={disabled || action.busy || chosen.length === 0} onClick={approve}>
          {action.busy ? t('studio.consent.sending') : t('studio.consent.approve')}
        </Button>
      </div>
    </section>
  )
}
