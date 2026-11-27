import type { CatalogueKey } from '../i18n/en'

/**
 * What the class panel says when the hand-off of a free-text class stopped on a stored refusal
 * (ADR 0038 amendment of 2026-11-24, decision 3). Pure. Each reason the backend can store on an
 * interpretation names whose it is, so the words never blame the request for an account or a site
 * limit, and the offered actions are only ones that can help:
 *
 * - `request`: the request is what to change (start screen with the same request; "Change my request"
 *   for a request for both sports, #1544);
 * - `account`: the instructor can act on the account (a link there);
 * - `site`: not available on this server now; nothing was charged and the request is kept; no
 *   retry, because a retry cannot succeed until the operator changes the installation (the
 *   not-evaluated plan case also links the account, where the plan is);
 * - `transient`: busy now; "Try again" asks the server once more;
 * - `state`: the class is past this step;
 * - `other`: a reason not in the table.
 *
 * `start.handoff.refused` (says nothing about the request) is the text of `other`.
 */
export type RefusalKind = 'request' | 'account' | 'site' | 'transient' | 'state' | 'other'
export type RefusalAction = 'interpretAgain' | 'retry' | 'account' | 'spotify' | 'startAgain' | 'changeRequest' | 'classes'

export type RefusalCopy = { kind: RefusalKind; text: CatalogueKey; actions: readonly RefusalAction[] }

export const REFUSALS: Readonly<Record<string, RefusalCopy>> = {
  invalid_input: { kind: 'request', text: 'ws.refused.invalid_input', actions: ['startAgain'] },
  too_large: { kind: 'request', text: 'ws.refused.too_large', actions: ['startAgain'] },
  not_connected: { kind: 'account', text: 'ws.refused.notConnected', actions: ['interpretAgain', 'account'] },
  needs_spotify: { kind: 'account', text: 'interp.reason.needs_spotify', actions: ['spotify', 'startAgain'] },
  // Nothing to connect: song picking is not available to the user; the start screen can drop the music.
  song_picking_unavailable: { kind: 'account', text: 'interp.reason.song_picking_unavailable', actions: ['startAgain'] },
  // Running's music is a playlist from the user's MOWL music: say so, offer the account, or the start screen to pick one.
  needs_mowl_playlist: { kind: 'account', text: 'interp.reason.needs_mowl_playlist', actions: ['account', 'startAgain'] },
  consent_missing: { kind: 'account', text: 'songs.start.consent_missing', actions: ['spotify', 'retry'] },
  quota: { kind: 'account', text: 'ws.refused.quota', actions: ['account'] },
  revision_limit: { kind: 'account', text: 'ws.refused.revision_limit', actions: ['account'] },
  feature_off: { kind: 'site', text: 'ws.refused.feature_off', actions: [] },
  soundless_not_evaluated: { kind: 'site', text: 'ws.refused.soundless_not_evaluated', actions: ['account'] },
  busy: { kind: 'transient', text: 'ws.refused.busy', actions: ['retry'] },
  pool_busy: { kind: 'transient', text: 'ws.refused.pool_busy', actions: ['retry'] },
  class_busy: { kind: 'transient', text: 'ws.refused.class_busy', actions: ['retry'] },
  not_ready: { kind: 'transient', text: 'ws.refused.not_ready', actions: ['retry'] },
  // No song is picked at random: the music question must be answered (a style, a playlist, or No music).
  music_unanswered: { kind: 'request', text: 'ws.refused.music_unanswered', actions: ['interpretAgain', 'startAgain'] },
  class_designed: { kind: 'state', text: 'ws.refused.class_designed', actions: ['classes'] },
  // A class is one sport (#1544): said kindly, with the start screen to change the request (no credit was used).
  two_sports: { kind: 'request', text: 'interp.reason.two_sports', actions: ['changeRequest'] },
}

export const UNKNOWN_REFUSAL: RefusalCopy = { kind: 'other', text: 'start.handoff.refused', actions: ['startAgain'] }

export function refusalCopy(reason: string | null): RefusalCopy {
  return (reason !== null ? REFUSALS[reason] : undefined) ?? UNKNOWN_REFUSAL
}
