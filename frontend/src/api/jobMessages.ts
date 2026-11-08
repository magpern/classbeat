import type { CatalogueKey } from '../i18n/en'
import { t } from '../i18n/t'
import { isSongKind } from './songs'
import { pickStageLabel, songFailure } from './songMessages'

// Plain sentences for a job's failure categories (ADR 0005 section 4) and stages. The category
// code is never shown, and no provider or model text ever reaches these pages. The words are the
// catalogue's (ADR 0023); every function looks them up when it is called, so a change of language
// shows at once. A component that calls one of them also calls `useT()`, so it renders again then.

export interface FailureText {
  title: string
  detail: string
  /** The user can fix it on the account page. */
  account?: boolean
}

interface FailureKeys {
  title: CatalogueKey
  detail: CatalogueKey
  account?: true
}

function words(keys: FailureKeys): FailureText {
  return { title: t(keys.title), detail: t(keys.detail), ...(keys.account ? { account: true } : {}) }
}

const FAILURES: Record<string, FailureKeys> = {
  upstream_unavailable: { title: 'job.failure.upstream_unavailable.title', detail: 'job.failure.upstream_unavailable.detail' },
  rate_limited: { title: 'job.failure.rate_limited.title', detail: 'job.failure.rate_limited.detail' },
  model_unavailable: { title: 'job.failure.model_unavailable.title', detail: 'job.failure.model_unavailable.detail' },
  lease_lost: { title: 'job.failure.lease_lost.title', detail: 'job.failure.lease_lost.detail' },
  operation_busy: { title: 'job.failure.operation_busy.title', detail: 'job.failure.operation_busy.detail' },
  not_connected: { title: 'job.failure.not_connected.title', detail: 'job.failure.not_connected.detail', account: true },
  needs_reconnect: { title: 'job.failure.needs_reconnect.title', detail: 'job.failure.needs_reconnect.detail', account: true },
  key_unavailable: { title: 'job.failure.key_unavailable.title', detail: 'job.failure.key_unavailable.detail', account: true },
  decrypt_failed: { title: 'job.failure.key_unavailable.title', detail: 'job.failure.key_unavailable.detail', account: true },
  invalid_input: { title: 'job.failure.invalid_input.title', detail: 'job.failure.invalid_input.detail' },
  account_disabled: { title: 'job.failure.account_disabled.title', detail: 'job.failure.account_disabled.detail' },
  model_refused: { title: 'job.failure.model_refused.title', detail: 'job.failure.model_refused.detail' },
  model_config: { title: 'job.failure.model_config.title', detail: 'job.failure.model_config.detail' },
  invalid_output: { title: 'job.failure.invalid_output.title', detail: 'job.failure.invalid_output.detail' },
  limit_exceeded: { title: 'job.failure.limit_exceeded.title', detail: 'job.failure.limit_exceeded.detail' },
  outbound_blocked: { title: 'job.failure.outbound_blocked.title', detail: 'job.failure.outbound_blocked.detail' },
  internal: { title: 'job.failure.outbound_blocked.title', detail: 'job.failure.outbound_blocked.detail' },
  program_changed: { title: 'job.failure.program_changed.title', detail: 'job.failure.program_changed.detail' },
  program_missing: { title: 'job.failure.program_missing.title', detail: 'job.failure.program_missing.detail' },
  music_changed: { title: 'job.failure.music_changed.title', detail: 'job.failure.music_changed.detail' },
  program_other_sport: { title: 'job.failure.program_other_sport.title', detail: 'job.failure.program_other_sport.detail' },
  use_change_songs: { title: 'job.failure.use_change_songs.title', detail: 'job.failure.use_change_songs.detail' },
  media_host_not_allowed: { title: 'job.failure.media_host_not_allowed.title', detail: 'job.failure.media_host_not_allowed.detail' },
  media_refused: { title: 'job.failure.media_refused.title', detail: 'job.failure.media_refused.detail' },
  analysis_failed: { title: 'job.failure.analysis_failed.title', detail: 'job.failure.analysis_failed.detail' },
  feature_off: { title: 'job.failure.feature_off.title', detail: 'job.failure.feature_off.detail' },
  analysis_limit: { title: 'job.failure.analysis_limit.title', detail: 'job.failure.analysis_limit.detail' },
  soundless_not_evaluated: { title: 'job.failure.soundless_not_evaluated.title', detail: 'job.failure.soundless_not_evaluated.detail' },
  music_unlinked: { title: 'job.failure.music_unlinked.title', detail: 'job.failure.music_unlinked.detail' },
}

const SOUNDLESS_INVALID: FailureKeys = {
  title: 'job.failure.soundlessInvalid.title',
  detail: 'job.failure.soundlessInvalid.detail',
}

const FALLBACK: FailureKeys = { title: 'job.failure.fallback.title', detail: 'job.failure.fallback.detail' }

const FEATURE_OFF: Record<'swedebeat' | 'running' | 'soundless', FailureKeys> = {
  soundless: { title: 'job.failure.featureOff.soundless.title', detail: 'job.failure.featureOff.soundless.detail' },
  swedebeat: { title: 'job.failure.featureOff.swedebeat.title', detail: 'job.failure.featureOff.swedebeat.detail' },
  running: { title: 'job.failure.featureOff.running.title', detail: 'job.failure.featureOff.running.detail' },
}

/**
 * The text of a failure category; `uncertain_write` has its own component. `feature` is what the
 * design was for, when the page knows it (the remembered form): it words `feature_off` for that
 * feature, never Swedebeat's sentence for a running class (ADR 0018, R1a review note).
 */
export function failureText(
  category: string | null,
  kind = '',
  feature: 'swedebeat' | 'running' | 'soundless' | null = null,
): FailureText {
  if (category === 'feature_off' && feature !== null) return words(FEATURE_OFF[feature])
  // A class with no music is cycling only and has no songs or mix: say what to change.
  if (category === 'invalid_input' && feature === 'soundless') return words(SOUNDLESS_INVALID)
  if (isSongKind(kind)) {
    const song = songFailure(category)
    if (song !== null) return song
  }
  return words(category !== null && Object.hasOwn(FAILURES, category) ? FAILURES[category]! : FALLBACK)
}

const STAGES: Record<string, CatalogueKey> = {
  music: 'job.stage.music',
  design: 'job.stage.design',
  revise: 'job.stage.revise',
  prepare: 'job.stage.prepare',
  read: 'job.stage.read',
  save: 'job.stage.save',
  verify: 'job.stage.verify',
  fit: 'job.stage.fit',
}

interface Step {
  stage: string
  key: CatalogueKey
}

/** Phase 1 of picking songs in the plain words of ADR 0013 section 6. */
const PICK_STEPS: readonly Step[] = [
  { stage: 'pool_search', key: 'job.step.pool_search' },
  { stage: 'pool_write', key: 'job.step.pool_write' },
  { stage: 'pool_analyse', key: 'job.step.pool_analyse' },
  { stage: 'choose', key: 'job.step.choose' },
]

/** After the songs are approved: PHASE_TWO of backend songs/phase2.py, in its order. */
const APPROVED_STEPS: readonly Step[] = [
  { stage: 'prepare', key: 'job.step.prepare' },
  { stage: 'create', key: 'songs.stage.create' },
  { stage: 'fill', key: 'songs.stage.fill' },
  { stage: 'attach', key: 'songs.stage.attach' },
  { stage: 'music', key: 'job.stage.music' },
  { stage: 'design', key: 'job.stage.design' },
]

/** `revise_songs` runs both parts in one job and ends with `revise`, not `design` (backend songs/revise.py STAGES). */
const REVISE_SONGS_STEPS: readonly Step[] = [
  ...PICK_STEPS,
  ...APPROVED_STEPS.filter((s) => s.stage !== 'design'),
  { stage: 'revise', key: 'job.stage.revise' },
]

const DRAFT_STEPS: readonly Step[] = [
  { stage: 'music', key: 'job.stage.music' },
  { stage: 'design', key: 'job.stage.design' },
]

/** `refit_draft` (ADR 0007 amendment "beat-one corrections"): no model, two stages. */
const REFIT_STEPS: readonly Step[] = [
  { stage: 'music', key: 'job.step.refitMusic' },
  { stage: 'fit', key: 'job.stage.fit' },
]

const SOUNDLESS_STEPS: readonly Step[] = [{ stage: 'design', key: 'job.stage.design' }]

/** The checklist of the Designing page for a job, in the order its stages run. */
export function designSteps(
  kind: string,
  approved: boolean,
  soundless = false,
): readonly { stage: string; label: string }[] {
  const steps = ((): readonly Step[] => {
    if (kind === 'refit_draft') return REFIT_STEPS
    // A soundless design reads no playlist and makes no MOWL or Spotify call (ADR 0007 amendment item 3).
    if (soundless && kind === 'design_draft') return SOUNDLESS_STEPS
    if (kind === 'revise_songs') return REVISE_SONGS_STEPS
    if (kind === 'design_with_songs') return approved ? APPROVED_STEPS : PICK_STEPS
    return DRAFT_STEPS
  })()
  return steps.map((s) => ({ stage: s.stage, label: t(s.key) }))
}

export function stageLabel(stage: string | null): string {
  const plain = PICK_STEPS.find((s) => s.stage === stage)
  if (plain !== undefined) return t(plain.key)
  const song = pickStageLabel(stage)
  if (song !== null) return song
  return t(stage !== null && Object.hasOwn(STAGES, stage) ? STAGES[stage]! : 'job.stage.default')
}

/**
 * The stage line while a Swedebeat mix is read. The `music` stage of such a design fetches and
 * analyses the mix, which can take minutes; no figure is promised (#572).
 */
export const mixStageLabel = (): string => t('job.mixStage')

/** Said while a design waits for a mix-analysis slot (`requeued` with the reason `analysis_slot`). */
export const waitingForSlot = (): string => t('job.waitingForSlot')

/** Said while a design is queued before any worker has started it. */
export const waitingToStart = (): string => t('job.waitingToStart')

/** The plain sentence under the progress line: leaving is safe. */
export const youCanLeave = (): string => t('job.youCanLeave')

const RETRIES: Record<string, CatalogueKey> = {
  upstream_unavailable: 'job.retry.upstream_unavailable',
  rate_limited: 'job.retry.rate_limited',
  model_unavailable: 'job.retry.model_unavailable',
}

export function retryLabel(category: string | null): string {
  return t(category !== null && Object.hasOwn(RETRIES, category) ? RETRIES[category]! : 'job.retry.default')
}

// Failure sentences for a change requested in the chat (`revise_draft`). The class keeps the
// version it had; the request stays in the conversation.
/** A request that only asks for other songs (ADR 0008 section 6): the way is Change songs. */
export const songsOnlyChange = (): string => t('job.use.changeSongs')
/** The same for a class whose music is a playlist of the user's own: it has no Change songs. */
export const songsOnlyOwnPlaylist = (): string => t('job.use.newClassForSongs')

/** The same for a running class: it is built on one MOWL playlist and has no song picking at all. */
export const musicOnlyRunning = (): string => t('job.use.otherMusicRunning')

/** The same while the class's own song pick is not done (waiting, expired, stopped). */
export const songsOnlyPickPending = (): string => t('job.use.finishSongs')

/** The same for a class with no music: there are no songs, so there is nothing to change. */
export const musicOnlySoundless = (): string => t('job.use.newClassForMusicSoundless')

const REVISE_FAILURES: Record<string, CatalogueKey> = {
  upstream_unavailable: 'job.revise.upstream_unavailable',
  rate_limited: 'job.revise.rate_limited',
  model_unavailable: 'job.revise.model_unavailable',
  lease_lost: 'job.revise.lease_lost',
  operation_busy: 'job.revise.operation_busy',
  not_connected: 'job.revise.not_connected',
  needs_reconnect: 'job.revise.needs_reconnect',
  key_unavailable: 'job.revise.key_unavailable',
  decrypt_failed: 'job.revise.key_unavailable',
  invalid_input: 'job.revise.invalid_input',
  account_disabled: 'job.revise.account_disabled',
  model_refused: 'job.revise.model_refused',
  model_config: 'job.revise.model_config',
  invalid_output: 'job.revise.invalid_output',
  limit_exceeded: 'job.revise.limit_exceeded',
  outbound_blocked: 'job.revise.outbound_blocked',
  internal: 'job.revise.outbound_blocked',
  music_unlinked: 'job.revise.music_unlinked',
  soundless_not_evaluated: 'job.revise.soundless_not_evaluated',
  use_change_songs: 'job.use.changeSongs',
  program_changed: 'job.revise.program_changed',
  program_missing: 'job.revise.program_missing',
  program_other_sport: 'job.revise.program_other_sport',
}

/** The sentence for a failed change request; the category code is never shown. */
export function reviseFailureText(category: string | null, soundless = false): string {
  // `feature_off` of a revise is the soundless switch only for a class with no music.
  if (soundless && category === 'feature_off') return t('job.revise.soundlessFeatureOff')
  return t(
    category !== null && Object.hasOwn(REVISE_FAILURES, category) ? REVISE_FAILURES[category]! : 'job.revise.default',
  )
}
