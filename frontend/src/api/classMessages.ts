import { ApiError } from './client'
import { errorMessage } from './messages'
import { planRefusalText, quotaText } from './planMessages'
import type { ChangeSummary } from './classes'
import type { CatalogueKey } from '../i18n/en'
import { t } from '../i18n/t'
import { clock } from '../lib/workout'

// Plain sentences for the class page: the 409 reasons of the class API and of `POST /api/jobs`
// for `revise_draft` (ADR 0007 sections 2, 3 and 12), the change summary in words, and labels.

const REASON_CODES = [
  'stale_base',
  'hash_mismatch',
  'class_busy',
  'revision_limit',
  'not_changeable',
  'chat_full',
  'not_found',
  'not_connected',
  'invalid_input',
  'call_may_be_running',
  'music_unlinked',
  'running_not_evaluated',
  'no_beat_data',
  'correction_limit',
  'nothing_pending',
  'refit_limit',
  'game_retired',
  'has_user_edits',
] as const

type ReasonCode = (typeof REASON_CODES)[number]

function isReasonCode(reason: string): reason is ReasonCode {
  return (REASON_CODES as readonly string[]).includes(reason)
}

/** A 409 or 404 means what the page shows is out of date: it shows the reason and reloads. */
export function isStale(error: unknown): boolean {
  return error instanceof ApiError && (error.status === 409 || error.status === 404)
}

export function classReasonText(error: unknown): string {
  const refusal = planRefusalText(error)
  if (refusal !== null) return refusal
  if (error instanceof ApiError && error.reason !== null && isReasonCode(error.reason)) {
    return t(`class.reason.${error.reason}`)
  }
  if (error instanceof ApiError && error.status === 409) {
    return t('class.reason.changedMeanwhile')
  }
  return errorMessage(error)
}

/** The text of a refused Make current: a plan-limit refusal is about the version, not the length to choose. */
export function currentReasonText(error: unknown): string {
  if (error instanceof ApiError && error.reason === 'quota') return quotaText(error, 'version')
  return classReasonText(error)
}

/** The name of a segment kind; a kind this page does not know is shown as it came. */
export function kindLabel(kind: string): string {
  switch (kind) {
    case 'warmup':
      return t('class.kind.warmup')
    case 'main':
      return t('class.kind.main')
    case 'recovery':
      return t('class.kind.recovery')
    case 'cooldown':
      return t('class.kind.cooldown')
    default:
      return kind
  }
}

/**
 * Every `provenance.origin` the server stores (`backend/tests/fixtures/revision_origins.json`, checked
 * against the server's classified origins there and against this map in `classMessages.test.ts`), with its label. A sketch
 * (ADR 0038 section 4) is labelled as one, first or changed; a revision with no origin or one this page
 * does not know reads "Version".
 */
export const ORIGIN_LABELS = {
  design_draft: 'class.origin.design_draft',
  revise_draft: 'class.origin.revise_draft',
  sketch: 'class.origin.sketch',
  design_with_songs: 'class.origin.other',
  revise_songs: 'class.origin.other',
  studio_songs: 'class.origin.other',
  duplicate: 'class.origin.other',
  beat_one: 'class.origin.other',
  swedebeat_match: 'class.origin.other',
  mix_choices: 'class.origin.other',
  user_edit: 'class.origin.other',
} as const satisfies Record<string, CatalogueKey>

export function originLabel(origin: string): string {
  if (!Object.hasOwn(ORIGIN_LABELS, origin)) return t('class.origin.other')
  return t(ORIGIN_LABELS[origin as keyof typeof ORIGIN_LABELS])
}

/** The backend's change summary in the page's own words; it is data, not text (ADR 0007 section 3). */
export function describeChange(change: ChangeSummary): string[] {
  const lines: string[] = []
  const { segmentsAdded: sa, segmentsRemoved: sr } = change
  if ((sa ?? 0) > 0 || (sr ?? 0) > 0) {
    lines.push(t('class.change.segments', { added: sa ?? 0, removed: sr ?? 0 }))
  }
  const { intervalsAdded: ia, intervalsRemoved: ir, intervalsChanged: ic } = change
  if ((ia ?? 0) > 0 || (ir ?? 0) > 0 || (ic ?? 0) > 0) {
    lines.push(t('class.change.intervals', { added: ia ?? 0, removed: ir ?? 0, changed: ic ?? 0 }))
  }
  if (change.durationBefore !== null && change.durationAfter !== null) {
    lines.push(
      change.durationBefore === change.durationAfter
        ? t('class.change.lengthSame', { length: clock(change.durationAfter) })
        : t('class.change.length', {
            from: clock(change.durationBefore),
            to: clock(change.durationAfter),
          }),
    )
  }
  if (change.tssBefore !== null && change.tssAfter !== null) {
    lines.push(
      change.tssBefore === change.tssAfter
        ? t('class.change.tssSame', { tss: change.tssAfter })
        : t('class.change.tss', { from: change.tssBefore, to: change.tssAfter }),
    )
  }
  if (change.textChanged) lines.push(t('class.change.text'))
  return lines
}
