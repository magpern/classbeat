import { ApiError } from './client'
import { spotifySentence } from './spotifyMessages'

// The words of song picking (ADR 0008 sections 5 and 9, ADR 0009 section 8). A category code, a
// provider message and a model text are never shown; every refusal and failure has a sentence.

/** The stages of phase 1 in the order they run, with the words the Designing page uses. */
export const PICK_STAGES: readonly { stage: string; label: string }[] = [
  { stage: 'pool_search', label: 'Finding songs that could fit' },
  { stage: 'pool_write', label: 'Writing your working playlist in Spotify' },
  { stage: 'pool_analyse', label: 'Waiting for MOWL to analyse the songs' },
  { stage: 'choose', label: 'Choosing the songs for your class' },
]

/** Phase 2 stages (after the approval) that no other job has. */
export const SONG_STAGE_LABELS: Record<string, string> = {
  create: 'Creating your playlist in Spotify',
  fill: 'Adding the approved songs to your playlist',
  attach: 'Adding your playlist to your MOWL music',
}

export function pickStageLabel(stage: string | null): string | null {
  const found = PICK_STAGES.find((s) => s.stage === stage)
  if (found !== undefined) return found.label
  return stage !== null && Object.hasOwn(SONG_STAGE_LABELS, stage) ? SONG_STAGE_LABELS[stage]! : null
}

/** What Approve songs answers with when it refuses (ADR 0008 section 5). */
export interface Refusal {
  text: string
  /** A final refusal cannot be retried: the page then offers no Approve button. */
  final: boolean
  /** The fix is on the account page (the Spotify and MOWL cards). */
  account?: boolean
  /** The user can ask for new songs instead. */
  newSongs?: boolean
}

const APPROVE_REFUSALS: Record<string, Refusal> = {
  expired: {
    text: 'These songs expired before they were approved. Nothing was written to your Spotify.',
    final: true,
    newSongs: true,
  },
  superseded: {
    text: 'A newer list of songs has replaced these, so they cannot be approved any more.',
    final: true,
  },
  hash_mismatch: {
    text: 'These songs changed after this page was loaded, so nothing was approved. Reload the page to see the current list.',
    final: true,
  },
  not_connected: {
    text: 'Spotify or MOWL is not connected. Connect it on your account page, then come back and approve.',
    final: false,
    account: true,
  },
  account_changed: {
    text: 'Your Spotify account is not the one these songs were chosen for. Ask for new songs.',
    final: true,
    newSongs: true,
  },
  not_waiting: {
    text: 'These songs can no longer be approved. They were approved already, or stopped.',
    final: true,
  },
  busy: {
    text: 'You already have designs running. Wait for one to finish, then approve.',
    final: false,
  },
  quota: { text: 'You have used up what your plan allows for now.', final: false },
  not_found: { text: 'These songs were not found, or they are not yours.', final: true },
}

const APPROVE_FALLBACK: Refusal = { text: 'That did not work. Try again.', final: false }

export function approveRefusal(error: unknown): Refusal {
  if (error instanceof ApiError && error.reason !== null && Object.hasOwn(APPROVE_REFUSALS, error.reason)) {
    return APPROVE_REFUSALS[error.reason]!
  }
  return APPROVE_FALLBACK
}

// What starting a pick, a change or a resume answers with (ADR 0008 section 9, 409 reasons).
const START_ERRORS: Record<string, string> = {
  song_picking_off: 'Picking songs is not available right now.',
  consent_missing: 'Allow the working playlist in your Spotify settings on your account page first.',
  pool_busy: 'Another song pick is still running. Wait for it to finish, then try again.',
  class_busy: 'This class already has a task running. Wait for it to finish, then try again.',
  stale_base: 'The class changed in the meantime. Reload the page and try again.',
  revision_limit: 'This class has reached the most versions it can have.',
  chat_full: 'The conversation of this class is full, so it cannot be changed any more.',
  not_song_picked: 'The music of this class was not picked by Claude, so its songs cannot be changed here.',
  invalid_input: 'The request was not accepted. Check the fields and try again.',
  not_connected: 'Spotify or MOWL is not connected. Connect it on your account page first.',
  receipt_invalid:
    'These songs cannot be turned into a playlist again: they were already used, or never approved. Pick songs again.',
  account_changed: 'Your Spotify account is not the one these songs were chosen for. Pick songs again.',
  write_unresolved:
    'An earlier step on Spotify is still undecided. Check it first, then create the playlist again.',
  quota: 'You have used up what your plan allows for now.',
  busy: 'You already have designs running. Wait for one to finish, then try again.',
  idempotency_mismatch: 'That did not work. Check the form and try again.',
}

export function startError(error: unknown): string {
  if (error instanceof ApiError && error.reason !== null && Object.hasOwn(START_ERRORS, error.reason)) {
    return START_ERRORS[error.reason]!
  }
  return 'That did not work. Try again.'
}

export function isClassBusy(error: unknown): boolean {
  return error instanceof ApiError && error.status === 409 && error.reason === 'class_busy'
}

export interface SongFailure {
  title: string
  detail: string
  /** The user can fix it on the account page. */
  account?: boolean
}

/**
 * The failure sentences of a song job, by category (ADR 0008 section 9, ADR 0009 section 8).
 * Categories that a song job shares with other jobs but words differently are here too.
 */
const SONG_FAILURES: Record<string, SongFailure> = {
  invalid_grant: {
    title: 'Spotify no longer accepts the connection',
    detail: spotifySentence('invalid_grant'),
    account: true,
  },
  app_misconfigured: {
    title: 'Spotify does not recognise this app setup',
    detail: spotifySentence('app_misconfigured'),
    account: true,
  },
  not_allowlisted: {
    title: 'Spotify refused this account',
    detail: spotifySentence('not_allowlisted'),
    account: true,
  },
  forbidden: {
    title: 'Spotify refused the request',
    detail: spotifySentence('forbidden'),
  },
  rate_limited: {
    title: 'Spotify or MOWL asked classbeat to slow down',
    detail: 'Wait a few minutes and try again.',
  },
  quota_exhausted: {
    title: 'Your Spotify app has used up its allowance',
    detail: 'Your Spotify app has used up its allowance for now. Try again in a few hours.',
  },
  upstream_unavailable: {
    title: 'Spotify or MOWL could not be reached',
    detail: 'classbeat tried several times. Try again in a few minutes.',
  },
  not_found: {
    title: 'A playlist is gone from Spotify',
    detail: 'The working playlist is gone from Spotify. Start again.',
  },
  scope_mismatch: {
    title: 'Spotify did not give the access classbeat needs',
    detail: spotifySentence('scope_mismatch'),
    account: true,
  },
  invalid_response: {
    title: 'Spotify did not give the access classbeat needs',
    detail: spotifySentence('invalid_response'),
    account: true,
  },
  account_changed: {
    title: 'This is not the Spotify account the songs were chosen for',
    detail: 'Your Spotify account is not the one these songs were chosen for. Ask for new songs.',
  },
  access_denied: {
    title: 'Spotify was not connected',
    detail: spotifySentence('access_denied'),
    account: true,
  },
  not_connected: {
    title: 'Spotify or MOWL is not connected',
    detail: 'Connect it on your account page, then start again.',
    account: true,
  },
  consent_missing: {
    title: 'The working playlist is not allowed yet',
    detail: 'Allow the working playlist in your Spotify settings first.',
    account: true,
  },
  pool_unusable: {
    title: 'Your working playlist was changed',
    detail: 'Your working playlist was changed: make it private again or set it up again.',
  },
  pool_missing: {
    title: 'Your working playlist was gone from Spotify',
    detail: 'Your working playlist was gone from Spotify. Try again.',
  },
  pool_not_synced: {
    title: 'MOWL has not caught up yet',
    detail: 'MOWL has not caught up with the working playlist; try again in a minute.',
  },
  playlist_mismatch: {
    title: 'Spotify’s playlist did not match the approved songs',
    detail: 'Spotify’s playlist did not match the approved songs; nothing was designed. Create it again.',
  },
  pool_too_small: {
    title: 'Not enough usable songs were found',
    detail: 'Not enough usable songs were found; try a wider style or era.',
  },
  pool_busy: {
    title: 'Another song pick is still running',
    detail: 'Another song pick is still running. Try again when it has finished.',
  },
}

export const SONG_FAILURE_CATEGORIES: readonly string[] = Object.keys(SONG_FAILURES)

/** The sentence of a song job's failure, or null when the generic table words it. */
export function songFailure(category: string | null): SongFailure | null {
  return category !== null && Object.hasOwn(SONG_FAILURES, category) ? SONG_FAILURES[category]! : null
}

// The undecided write of the final playlist (ADR 0008 section 2, ADR 0005 section 7).
export const PLAYLIST_UNCERTAIN_TITLE =
  'classbeat could not tell whether your playlist was written in Spotify'
export const PLAYLIST_UNCERTAIN_TEXT: readonly string[] = [
  'Spotify did not answer in time while classbeat was writing the playlist for your approved songs. The playlist may or may not be in your Spotify now.',
  'Check again asks Spotify what is there. Do it anyway writes without knowing.',
]
export const PLAYLIST_OVERWRITE_WARNING =
  'If the playlist is already in your Spotify you may end up with two playlists of the same name. You can delete the extra one in Spotify. Only go on if you are sure it was not written.'
export const PLAYLIST_OUTCOME = {
  found: 'The playlist is in your Spotify. Create the playlist again to carry on with your class.',
  nothing: 'Nothing was written to Spotify. Create the playlist again to carry on with your class.',
  decided: 'You decided to write it again. Create the playlist again to carry on with your class.',
  still_unsure: 'Spotify still does not say. Wait a few minutes and check again.',
} as const

export type PlaylistOutcome = keyof typeof PLAYLIST_OUTCOME
