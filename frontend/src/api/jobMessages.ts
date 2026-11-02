import { isSongKind } from './songs'
import { pickStageLabel, songFailure } from './songMessages'

// Plain sentences for a job's failure categories (ADR 0005 section 4) and stages. The category
// code is never shown, and no provider or model text ever reaches these pages.

export interface FailureText {
  title: string
  detail: string
  /** The user can fix it on the account page. */
  account?: boolean
}

const FAILURES: Record<string, FailureText> = {
  upstream_unavailable: {
    title: 'MOWL could not be reached',
    detail: 'classbeat tried several times. Your class was not designed. Try again in a few minutes.',
  },
  rate_limited: {
    title: 'MOWL asked classbeat to slow down',
    detail: 'Your class was not designed. Wait a few minutes and try again.',
  },
  model_unavailable: {
    title: 'The designer is busy',
    detail: 'The service that designs the class could not take the request. Try again in a few minutes.',
  },
  lease_lost: {
    title: 'The design was interrupted',
    detail: 'It was interrupted too many times. Try again.',
  },
  operation_busy: {
    title: 'Another design is using this playlist',
    detail: 'Another design of yours was still adding the same playlist to MOWL. Try again in a moment.',
  },
  not_connected: {
    title: 'MOWL is not connected',
    detail: 'Connect MOWL on your account page, then start again.',
    account: true,
  },
  needs_reconnect: {
    title: 'MOWL no longer accepts the stored sign-in',
    detail: 'Connect MOWL again on your account page, then start again.',
    account: true,
  },
  key_unavailable: {
    title: 'The stored MOWL sign-in could not be read',
    detail: 'Connect MOWL again on your account page, then start again.',
    account: true,
  },
  decrypt_failed: {
    title: 'The stored MOWL sign-in could not be read',
    detail: 'Connect MOWL again on your account page, then start again.',
    account: true,
  },
  invalid_input: {
    title: 'The request could not be used',
    detail: 'Check the form and start again.',
  },
  account_disabled: {
    title: 'Your account cannot start designs right now',
    detail: 'Contact the person who invited you.',
  },
  model_refused: {
    title: 'The designer declined this brief',
    detail: 'Change the description and start again.',
  },
  model_config: {
    title: 'classbeat cannot design classes right now',
    detail: 'This is a problem on our side. Try again later.',
  },
  invalid_output: {
    title: 'The designer did not produce a usable class',
    detail: 'Try again, or simplify the description.',
  },
  limit_exceeded: {
    title: 'This brief needed more work than one design may use',
    detail: 'Try a shorter or simpler class.',
  },
  outbound_blocked: {
    title: 'Something went wrong on our side',
    detail: 'Try again later.',
  },
  internal: {
    title: 'Something went wrong on our side',
    detail: 'Try again later.',
  },
  // ADR 0007 section 12: not retryable.
  program_changed: {
    title: 'This workout was changed in MOWL',
    detail: 'This workout was changed in MOWL after classbeat saved it.',
  },
  program_missing: {
    title: 'This workout is gone from MOWL',
    detail: 'This workout is no longer in your MOWL account.',
  },
  // ADR 0008 section 6: the request only asked for other songs; nothing was designed.
  use_change_songs: {
    title: 'This request is only about the songs',
    detail: 'Nothing was changed. Use Change songs on the class page to pick other ones.',
  },
  // ADR 0016 sections 3 and 4: a Swedebeat mix; never a host, link or file text.
  media_host_not_allowed: {
    title: 'New Swedebeat mixes cannot be analysed here yet',
    detail:
      'The mix was added to your MOWL account, but it could not be analysed. Mixes you analysed before still work. Try one of those, or another kind of music.',
  },
  media_refused: {
    title: 'The audio of this mix could not be fetched',
    detail: 'Nothing was designed. Try again later.',
  },
  analysis_failed: {
    title: 'The audio of this mix could not be analysed',
    detail: 'Try another mix.',
  },
  // ADR 0016 sections 2 and 7: the flag went off before the run; the daily analysis limit.
  feature_off: {
    title: 'Swedebeat is switched off right now',
    detail: 'Nothing was designed. Try again later, or use another kind of music.',
  },
  analysis_limit: {
    title: 'Too many new Swedebeat mixes today',
    detail:
      'You have analysed 10 new mixes in the last 24 hours. Mixes analysed before still work; try a new one tomorrow.',
  },
  music_unlinked: {
    title: 'The music of this version is not in your MOWL account',
    detail:
      'The music of this version is not in your connected MOWL account. Change it and save the new version.',
  },
}

const FALLBACK: FailureText = {
  title: 'The design did not finish',
  detail: 'Try again later.',
}

/** The text of a failure category; `uncertain_write` has its own component. */
export function failureText(category: string | null, kind = ''): FailureText {
  if (isSongKind(kind)) {
    const song = songFailure(category)
    if (song !== null) return song
  }
  return category !== null && Object.hasOwn(FAILURES, category) ? FAILURES[category]! : FALLBACK
}

const STAGES: Record<string, string> = {
  music: 'Reading your playlist',
  design: 'Building your class',
  revise: 'Changing the class',
  prepare: 'Checking the class and your MOWL account',
  read: 'Reading the workout in MOWL',
  save: 'Writing to MOWL',
  verify: 'Checking what MOWL kept',
}

/** Phase 1 of picking songs in the plain words of ADR 0013 section 6. */
export const PICK_STEPS: readonly { stage: string; label: string }[] = [
  { stage: 'pool_search', label: 'Finding songs that fit' },
  { stage: 'pool_write', label: 'Saving your working playlist' },
  { stage: 'pool_analyse', label: 'MOWL is listening to the music' },
  { stage: 'choose', label: 'Choosing the best songs for each part' },
]

/** After the songs are approved: PHASE_TWO of backend songs/phase2.py, in its order. */
const APPROVED_STEPS: readonly { stage: string; label: string }[] = [
  { stage: 'prepare', label: 'Checking your accounts' },
  { stage: 'create', label: 'Creating your playlist in Spotify' },
  { stage: 'fill', label: 'Adding the approved songs to your playlist' },
  { stage: 'attach', label: 'Adding your playlist to your MOWL music' },
  { stage: 'music', label: 'Reading your playlist' },
  { stage: 'design', label: 'Building your class' },
]

/** `revise_songs` runs both parts in one job and ends with `revise`, not `design` (backend songs/revise.py STAGES). */
const REVISE_SONGS_STEPS: readonly { stage: string; label: string }[] = [
  ...PICK_STEPS,
  ...APPROVED_STEPS.filter((s) => s.stage !== 'design'),
  { stage: 'revise', label: 'Changing the class' },
]

const DRAFT_STEPS: readonly { stage: string; label: string }[] = [
  { stage: 'music', label: 'Reading your playlist' },
  { stage: 'design', label: 'Building your class' },
]

/** The checklist of the Designing page for a job, in the order its stages run. */
export function designSteps(kind: string, approved: boolean): readonly { stage: string; label: string }[] {
  if (kind === 'revise_songs') return REVISE_SONGS_STEPS
  if (kind === 'design_with_songs') return approved ? APPROVED_STEPS : PICK_STEPS
  return DRAFT_STEPS
}

export function stageLabel(stage: string | null): string {
  const plain = PICK_STEPS.find((s) => s.stage === stage)
  if (plain !== undefined) return plain.label
  const song = pickStageLabel(stage)
  if (song !== null) return song
  return stage !== null && Object.hasOwn(STAGES, stage) ? STAGES[stage]! : 'Getting started'
}

const RETRIES: Record<string, string> = {
  upstream_unavailable: 'MOWL did not answer. Trying again shortly.',
  rate_limited: 'MOWL asked classbeat to slow down. Trying again shortly.',
  model_unavailable: 'The designer is busy. Trying again shortly.',
}

export function retryLabel(category: string | null): string {
  return category !== null && Object.hasOwn(RETRIES, category)
    ? RETRIES[category]!
    : 'Something went wrong. Trying again shortly.'
}

// Failure sentences for a change requested in the chat (`revise_draft`). The class keeps the
// version it had; the request stays in the conversation.
/** A request that only asks for other songs (ADR 0008 section 6): the way is Change songs. */
export const USE_CHANGE_SONGS =
  'This request is only about the songs, so nothing changed. Use Change songs to pick other ones.'
/** The same for a class whose music is a playlist of the user's own: it has no Change songs. */
export const USE_NEW_CLASS_FOR_SONGS =
  'This request is only about the songs, so nothing changed. This class uses a playlist of your own; to use other music, design a new class.'

/** The same while the class's own song pick is not done (waiting, expired, stopped). */
export const USE_FINISH_SONGS =
  'This request is only about the songs, so nothing changed. Finish or retry the song pick on this page first; then use Change songs.'

const REVISE_FAILURES: Record<string, string> = {
  upstream_unavailable: 'MOWL could not be reached. Nothing changed. Try again in a few minutes.',
  rate_limited: 'MOWL asked classbeat to slow down. Nothing changed. Wait a few minutes and try again.',
  model_unavailable: 'The designer is busy. Nothing changed. Try again in a few minutes.',
  lease_lost: 'The change was interrupted too many times. Nothing changed. Try again.',
  operation_busy: 'Another design of yours was still using the same playlist. Nothing changed. Try again in a moment.',
  not_connected: 'MOWL is not connected. Connect it on your account page, then ask again.',
  needs_reconnect: 'MOWL no longer accepts the stored sign-in. Connect it again on your account page, then ask again.',
  key_unavailable: 'The stored MOWL sign-in could not be read. Connect MOWL again on your account page.',
  decrypt_failed: 'The stored MOWL sign-in could not be read. Connect MOWL again on your account page.',
  invalid_input: 'That request could not be used. Nothing changed. Ask in different words.',
  account_disabled: 'Your account cannot change classes right now. Contact the person who invited you.',
  model_refused: 'The designer declined this request. Nothing changed. Ask in different words.',
  model_config: 'classbeat cannot change classes right now. This is a problem on our side. Try again later.',
  invalid_output: 'The designer did not produce a usable change, or one that changes nothing. Nothing changed. Try again, or ask in different words.',
  limit_exceeded: 'This request needed more work than one change may use. Nothing changed. Ask for something smaller.',
  outbound_blocked: 'Something went wrong on our side. Nothing changed. Try again later.',
  internal: 'Something went wrong on our side. Nothing changed. Try again later.',
  music_unlinked: 'The music of this version is not in your connected MOWL account. Change it and save the new version.',
  use_change_songs: USE_CHANGE_SONGS,
  program_changed: 'This workout was changed in MOWL after classbeat saved it.',
  program_missing: 'This workout is no longer in your MOWL account.',
}

/** The sentence for a failed change request; the category code is never shown. */
export function reviseFailureText(category: string | null): string {
  return category !== null && Object.hasOwn(REVISE_FAILURES, category)
    ? REVISE_FAILURES[category]!
    : 'The change did not finish. Nothing changed. Try again later.'
}
