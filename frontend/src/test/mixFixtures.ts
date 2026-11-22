import { CLASS_PATH } from './classFixtures'

// Fixtures of the DJ mix screen (ADR 0036 amendment B): the mix fields a timeline answers, and the
// read-only preview (`POST .../mix-choices/preview`).

export const DIGEST = '5'.repeat(64)
export const OTHER_DIGEST = '6'.repeat(64)
export const PROPOSAL = 'f1111111-1111-4111-8111-111111111111'

/** The fields every timeline answer carries: the draft is the whole-songs form, with a proposal. */
export const DRAFT_MIX = { spec_digest: DIGEST, proposal_revision_id: PROPOSAL, is_plain: true, choices: null }

export const PREVIEW_PATH = `${CLASS_PATH}/mix-choices/preview`

export function suggestion(position: number, over: Record<string, unknown> = {}) {
  return {
    position,
    length_sec: 245,
    timing_class: 'exact',
    pieces: [{ start_sec: 0, end_sec: 190 }],
    ends_on_phrase: [true],
    full_only: null,
    ...over,
  }
}

export const SUGGESTIONS = [suggestion(0), suggestion(1, { timing_class: 'approximate', full_only: 'timing_approximate' }), suggestion(2)]

/** A preview answer: the draft's own digest, 22 minutes against a class of 22, nothing refused. */
export function previewBody(over: Record<string, unknown> = {}) {
  return {
    revision_id: 'a1111111-1111-4111-8111-111111111111',
    proposal_revision_id: PROPOSAL,
    target_sec: 1320,
    allowed: { low_sec: 1188, high_sec: 1452 },
    suggestions: SUGGESTIONS,
    spec_digest: DIGEST,
    windows: [
      { position: 0, start_sec: 0, end_sec: 245 },
      { position: 1, start_sec: 0, end_sec: 245 },
      { position: 2, start_sec: 0, end_sec: 245 },
    ],
    crossfades: [0, 0],
    total_sec: 1320,
    refusal: null,
    ...over,
  }
}
