import type { CatalogueKey } from '../../i18n/en'

// The reason codes the edit API answers (`classes/edit_routes.py` and what it relays from the edit
// policies), each with its own sentence. An unknown code is never shown as a code: it gets the
// generic sentence.

export const EDIT_REASONS = [
  'class_busy',
  'revision_limit',
  'edit_unavailable',
  'audio_edit_unavailable',
  'no_proposal',
  'music_changed',
  'mix_not_applied',
  'block_locked',
  'nothing_changed',
  'class_length_out_of_tolerance',
  'plan_class_too_long',
  'ftp_test_fixed',
  'anchor_out_of_reach',
  'invalid_input',
  'invalid_edit',
  'too_many_blocks',
  'split_out_of_range',
  'duration_out_of_range',
  'block_too_long',
  'overlay_too_large',
  'cut_out_of_range',
  'cut_on_inexact_track',
  'crossfades_overlap',
  'off_beat_cut',
  'loop_not_on_phrase',
  'loop_needs_exact_timing',
  'locked_block_conflict',
  'overlay_violated',
  'hash_mismatch',
  'not_ready',
  'quota',
  'internal',
] as const

export type EditReason = (typeof EDIT_REASONS)[number]

export function reasonKey(reason: string): CatalogueKey {
  const known = (EDIT_REASONS as readonly string[]).includes(reason)
  return (known ? `editor.reason.${reason as EditReason}` : 'editor.reason.unknown') satisfies CatalogueKey
}
