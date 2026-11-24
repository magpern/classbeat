import { useState } from 'react'
import type { ClassSongs } from '../../api/classSongs'
import { recordedChoices } from '../../api/studioMix'
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
import {
  changeSongsOffered,
  choiceSummary,
  fromRecorded,
  replanOffered,
  stepOf,
  toBody,
  type MixState,
  type Step,
} from './mixChoices'
import { StudioMixSection } from './StudioMixSection'
import { StudioOutputsUnread } from './StudioOutputsUnread'
import { StudioMixTotal } from './StudioMixTotal'
import { useMixActions } from './useMixActions'
import { useMixPreview, type PreviewAnswer } from './useMixPreview'

/**
 * The output choice and its consent (ADR 0036 section 6): the defaults of the matrix, the facts
 * the user accepts (SwedeBeat has no delete; a published playlist shows their name and email; what
 * a merged mix shows others is unconfirmed), and the approval of this draft for those outputs.
 *
 * `enabled` is the outputs the server runs for the user (`GET /outputs`), so only those are
 * offered and the defaults fall back to what is on; an empty list (a design-only studio, ADR 0036
 * section 10) says none is on; null (the list could not be read) offers none, with a notice and a
 * retry, so nothing is picked that the server would refuse. A draft whose mix repeats a song
 * (`timeline.hasRepeats`) is mix-only (ADR 0036 amendment A8): the SwedeBeat playlist is shown off
 * and disabled with the reason, and the server refuses it too. The per-output states and their
 * actions are `StudioOutputStates`.
 *
 * Whole songs is the default (ADR 0036 amendment B, B1): the Spotify and SwedeBeat playlists play
 * each song once, whole. **DJ mix** is its own output, off by default, with the choices of
 * `StudioMixChoices`. With a SwedeBeat output the draft must be the form the screen shows: the
 * preview's spec digest is compared with the draft's (B10), and until they are equal the button is
 * **Apply these choices** (a new draft; nothing is applied on open), then **Approve**.
 */
export function StudioOutputs({
  classId,
  timeline,
  songs,
  disabled,
  enabled,
  onRetry,
  limitReached = false,
}: {
  classId: string
  timeline: Timeline
  /** The class's approved song list state, to name the songs of the mix rows. */
  songs: ClassSongs
  /** Another job of the class is open. */
  disabled: boolean
  /** The outputs the server runs; null when the list could not be read. */
  enabled: readonly Output[] | null
  /** Reads the enabled list again. */
  onRetry: () => void
  /** The class's last approval stopped at the limit: a notice only, Approve stays on. */
  limitReached?: boolean
}) {
  const t = useT()
  const { navigate } = useRouter()
  const offered = OUTPUTS.filter((o) => enabled?.includes(o) === true)
  const mixOnly = timeline.hasRepeats
  const allowed = (o: Output) => offered.includes(o) && !(mixOnly && o === 'swedebeat_playlist')
  // The choices of an applied draft come back as toggles, never applied by themselves (B3, B6).
  const recorded = recordedChoices(timeline.choices)
  const [mix, setMix] = useState<MixState>(() => fromRecorded(recorded))
  const [picked, setChosen] = useState<Output[]>(() =>
    OUTPUTS.filter((o) => allowed(o) && (DEFAULT_OUTPUTS.includes(o) || (o === 'swedebeat_mix' && recorded?.dj_mix === true))),
  )
  // An output that went off after the page loaded is dropped from the choice (and MOWL goes with a mix that is off),
  // and so is the SwedeBeat playlist of a draft that repeats a song.
  const chosen = picked.filter((o) => allowed(o) && (o !== 'mowl' || offered.includes('swedebeat_mix')))
  const action = useAction()
  const has = (output: Output) => chosen.includes(output)
  const djMix = has('swedebeat_mix')
  const publishes = has('swedebeat_playlist') || djMix
  // Every SwedeBeat output binds a draft that is the chosen form (the server's `mix_not_applied`).
  const needsForm = publishes
  const choices = toBody(djMix, mix)
  const hasProposal = timeline.proposalRevisionId !== null
  const preview = useMixPreview(classId, timeline.revisionId, timeline.contentHash, choices, needsForm && hasProposal)
  const actions = useMixActions(classId, timeline, choices)
  const noProposal: PreviewAnswer = { status: 'failed', reason: 'no_proposal', message: '' }
  const answer = hasProposal ? preview.answer : noProposal
  const step: Step = needsForm ? stepOf(answer, timeline.specDigest) : 'ready'
  const ready = step === 'ready'
  // While the answer for these choices is awaited the consent stays and Approve waits, disabled.
  const approving = ready || step === 'checking'
  const ahead = answer.status === 'ready' ? answer.preview : null
  const reason = (answer.status === 'failed' ? answer.reason : (ahead?.refusal ?? null)) ?? actions.refused
  const replan = reason !== null && replanOffered(reason, djMix, mix, preview.suggestions)
  const busy = action.busy || actions.busy
  const error = action.error ?? actions.error
  const summary =
    ready && djMix && ahead?.windows
      ? choiceSummary(ahead.windows, preview.suggestions, timeline.crossfades)
      : null

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
                  disabled={locked || repeats || busy || disabled}
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
      {djMix ? (
        <StudioMixSection
          classId={classId}
          songs={songs}
          suggestions={preview.suggestions}
          state={mix}
          windows={ahead?.windows ?? null}
          disabled={busy || disabled}
          onChange={setMix}
        />
      ) : null}
      {needsForm ? (
        <StudioMixTotal
          classId={classId}
          answer={answer}
          reason={reason}
          replan={replan}
          changeSongs={reason !== null && changeSongsOffered(reason, replan)}
          busy={busy || disabled}
          onReplan={actions.replan}
        />
      ) : null}
      {enabled === null ? (
        <StudioOutputsUnread onRetry={onRetry} />
      ) : chosen.length === 0 ? (
        <Notice>{t(offered.length === 0 ? 'studio.out.noneEnabled' : 'studio.out.none')}</Notice>
      ) : !approving ? (
        <p className="m-0 text-muted" data-testid="studio-apply-first">
          {t('studio.mix.applyFirst')}
        </p>
      ) : (
        <div className="flex flex-col gap-1" data-testid="studio-consent">
          <p className="m-0 font-medium text-text">{t('studio.consent.title')}</p>
          <p className="m-0 text-text">
            {t('studio.consent.chosen', { outputs: chosen.map((o) => t(`studio.out.${o}`)).join(', ') })}
          </p>
          {publishes ? <p className="m-0 text-muted">{t('studio.consent.noDelete')}</p> : null}
          {has('swedebeat_playlist') ? <p className="m-0 text-muted">{t('studio.consent.publicPlaylist')}</p> : null}
          {has('swedebeat_playlist') && djMix ? <p className="m-0 text-muted">{t('studio.consent.playlistFollowsMix')}</p> : null}
          {summary !== null ? (
            <p className="m-0 text-text" data-testid="studio-mix-summary">
              {t('studio.consent.mixChoices', { trimmed: summary.trimmed, cuts: summary.hardCuts })}
            </p>
          ) : null}
          {djMix ? <p className="m-0 text-muted">{t('studio.consent.mix')}</p> : null}
          {mixOnly ? <p className="m-0 text-muted">{t('studio.consent.repeats')}</p> : null}
        </div>
      )}
      {limitReached ? <Notice>{t('studio.os.limitReached')}</Notice> : null}
      {error !== null ? <Notice kind="error">{error}</Notice> : null}
      <div>
        {approving ? (
          <Button disabled={disabled || busy || chosen.length === 0 || !ready} onClick={approve}>
            {busy ? t('studio.consent.sending') : t('studio.consent.approve')}
          </Button>
        ) : (
          <Button disabled={disabled || busy || chosen.length === 0 || step !== 'apply'} onClick={actions.apply}>
            {busy ? t('studio.mix.applying') : t('studio.mix.apply')}
          </Button>
        )}
      </div>
    </section>
  )
}
