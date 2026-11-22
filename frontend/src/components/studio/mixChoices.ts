import type { MixChoicesBody, MixPreview, MixSuggestion, MixWindow } from '../../api/studioMix'

// The pure model of the DJ mix screen (ADR 0036 amendment B, B2 to B7): the user's choices as
// local state, the request they make to the preview, what the preview means for the buttons and
// when a re-plan is offered. No I/O. The server re-derives and re-checks everything (B4, B9).

/** The choices under DJ mix; DJ mix itself is the `swedebeat_mix` output, so it is not state here. */
export interface MixState {
  blend: boolean
  trim: boolean
  keepWhole: readonly number[]
  hardIn: readonly number[]
  hardOut: readonly number[]
}

/** B2: Blend and Trim are on when DJ mix is switched on, with no per-song choice. */
export const DEFAULT_MIX: MixState = { blend: true, trim: true, keepWhole: [], hardIn: [], hardOut: [] }

type SetName = 'keepWhole' | 'hardIn' | 'hardOut'

const sorted = (positions: Iterable<number>): number[] => [...new Set(positions)].sort((a, b) => a - b)

/** The request body: with DJ mix off it is the whole-songs form, with no switch and no per-song choice (B4). */
export function toBody(djMix: boolean, state: MixState): MixChoicesBody {
  if (!djMix) return { dj_mix: false, blend: false, trim: false, keep_whole: [], hard_cut_in: [], hard_cut_out: [] }
  return {
    dj_mix: true,
    blend: state.blend,
    trim: state.trim,
    keep_whole: sorted(state.keepWhole),
    hard_cut_in: sorted(state.hardIn),
    hard_cut_out: sorted(state.hardOut),
  }
}

/** The screen's state from the recorded choices of an applied draft (toggles only, never applied by themselves). */
export function fromRecorded(body: MixChoicesBody | null): MixState {
  if (body === null || !body.dj_mix) return DEFAULT_MIX
  return {
    blend: body.blend,
    trim: body.trim,
    keepWhole: body.keep_whole,
    hardIn: body.hard_cut_in,
    hardOut: body.hard_cut_out,
  }
}

export function setPositions(state: MixState, name: SetName, positions: readonly number[], on: boolean): MixState {
  const next = new Set(state[name])
  for (const position of positions) {
    if (on) next.add(position)
    else next.delete(position)
  }
  return { ...state, [name]: sorted(next) }
}

/** Bulk: keep the selected songs whole. */
export function keepWhole(state: MixState, positions: readonly number[]): MixState {
  return setPositions(state, 'keepWhole', positions, true)
}

/** Bulk: hard cuts into and out of the selected songs. */
export function hardCut(state: MixState, positions: readonly number[]): MixState {
  return setPositions(setPositions(state, 'hardIn', positions, true), 'hardOut', positions, true)
}

/** Bulk: the selected songs back to classbeat's suggestion (no choice of their own). */
export function backToSuggestion(state: MixState, positions: readonly number[]): MixState {
  let next = state
  for (const name of ['keepWhole', 'hardIn', 'hardOut'] as const) next = setPositions(next, name, positions, false)
  return next
}

/** Songs that cannot be trimmed whatever is chosen (B5): their trim toggle is not offered. */
export const fullOnlyPositions = (suggestions: readonly MixSuggestion[]): number[] =>
  suggestions.filter((s) => s.fullOnly !== null).map((s) => s.position)

/** A song with more than one piece (a repeat or a loop): keeping it whole plays it once (B7). */
export const isRepeated = (suggestion: MixSuggestion): boolean => suggestion.pieces.length > 1

export function sameChoices(a: MixChoicesBody, b: MixChoicesBody): boolean {
  return JSON.stringify(a) === JSON.stringify(b)
}

// What the preview means -------------------------------------------------------------------------

/**
 * Where the screen stands (B6, B10): `checking` (no answer for these choices yet), `ready` (the
 * current draft is this form: Approve), `apply` (a new draft is needed: Apply these choices),
 * `refused` (these choices are refused: the reason), `no_proposal` (nothing to apply to: Re-plan)
 * or `unavailable` (the preview could not be read).
 */
export type Step = 'checking' | 'ready' | 'apply' | 'refused' | 'no_proposal' | 'unavailable'

export function stepOf(
  answer: { status: 'loading' } | { status: 'failed'; reason: string | null; message?: string } | { status: 'ready'; preview: MixPreview },
  draftDigest: string,
): Step {
  if (answer.status === 'loading') return 'checking'
  if (answer.status === 'failed') return answer.reason === 'no_proposal' ? 'no_proposal' : 'unavailable'
  if (answer.preview.refusal !== null) return answer.preview.refusal === 'no_proposal' ? 'no_proposal' : 'refused'
  return answer.preview.specDigest === draftDigest ? 'ready' : 'apply'
}

const REPLAN_ALWAYS: readonly string[] = ['workout_does_not_fit', 'plan_not_allowed', 'no_proposal']
const REPLAN_LENGTH: readonly string[] = ['class_length_out_of_tolerance', 'plan_class_too_long']

/**
 * Re-plan the other songs is a `revise_draft` with the choices (B6). Always for the reasons that
 * need a re-design; for a class that is too long or too short only while Trim songs is on and some
 * song can still be trimmed (a song kept whole or full-only cannot), else Change songs.
 */
export function replanOffered(
  reason: string,
  djMix: boolean,
  state: MixState,
  suggestions: readonly MixSuggestion[],
): boolean {
  if (REPLAN_ALWAYS.includes(reason)) return true
  if (!REPLAN_LENGTH.includes(reason)) return false
  const trimmable = suggestions.some((s) => s.fullOnly === null && !state.keepWhole.includes(s.position))
  return djMix && state.trim && trimmable
}

export const changeSongsOffered = (reason: string, replan: boolean): boolean => REPLAN_LENGTH.includes(reason) && !replan

// The one-line summary of the choices, from the draft itself (B10) ----------------------------------

/** Songs shorter than the whole song, and hard cuts (a change with no crossfade), of a draft's spec. */
export function choiceSummary(
  windows: readonly MixWindow[],
  suggestions: readonly MixSuggestion[],
  crossfades: readonly { sec: number }[],
): { trimmed: number; hardCuts: number } {
  const length = new Map(suggestions.map((s) => [s.position, s.lengthSec]))
  const trimmed = new Set(
    windows.filter((w) => w.startSec > 0.5 || w.endSec < (length.get(w.position) ?? Infinity) - 0.5).map((w) => w.position),
  )
  return { trimmed: trimmed.size, hardCuts: crossfades.filter((c) => c.sec === 0).length }
}
