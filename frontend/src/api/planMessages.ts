import { ApiError } from './client'
import { isObject, type JsonObject } from './decode'
import type { CatalogueKey } from '../i18n/en'
import { t } from '../i18n/t'
import { errorMessage } from './messages'

// Plain sentences for the plan refusals (ADR 0011 section 9): 409 `quota` with its kind, limit,
// used count, period end and plan; 429 `busy` with the plan's limit; and the redeem and trial
// refusals. Numbers come from the server's body and are checked; nothing from it is markup.

/** The catalogue's name of a month (0 is January). */
function monthName(index: number): string {
  return t(`month.long.${String(index + 1)}` as CatalogueKey)
}

function parse(iso: unknown): Date | null {
  if (typeof iso !== 'string') return null
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? null : date
}

/** "1 November": the day a period ends (the start of the next UTC month). */
export function dayText(iso: unknown): string | null {
  const date = parse(iso)
  return date === null
    ? null
    : t('plan.dayMonth', { day: date.getUTCDate(), month: monthName(date.getUTCMonth()) })
}

/** "October": the UTC month of a period that ends at `periodEnd`. */
export function periodMonth(periodEnd: unknown): string | null {
  const date = parse(periodEnd)
  return date === null ? null : monthName(new Date(date.getTime() - 1).getUTCMonth())
}

function detailOf(error: ApiError): JsonObject {
  const body: unknown = error.body
  if (!isObject(body)) return {}
  return isObject(body.detail) ? body.detail : {}
}

function whole(value: unknown): number | null {
  return typeof value === 'number' && Number.isInteger(value) && value >= 0 ? value : null
}

const FEATURES: Record<string, CatalogueKey> = {
  games: 'plan.feature.games',
  races: 'plan.feature.races',
  ftp_tests: 'plan.feature.ftp_tests',
}

/**
 * The sentence for a 409 `quota`. The kind is the body's `quota` field, defaulting to `classes`;
 * `cost` means the month's model budget is used up; `length` (#496) that the class is longer than
 * the plan allows (`limit` in minutes); `feature` that the plan does not include `feature`.
 * `target` says what the refused request was about: a `new` class (the default: choose a shorter
 * length), an older `version` made current ("choose another version"), or a `save` of the current
 * version to MOWL, which is not a place to choose a length at all.
 */
export function quotaText(error: ApiError, target: 'new' | 'version' | 'save' = 'new'): string {
  const detail = detailOf(error)
  // What to do about a plan refusal of Save or Update: the version itself has to change.
  const ways = t('plan.saveWays')
  if (detail.quota === 'length') {
    const limit = whole(detail.limit)
    const params = { limit: limit ?? 0, ways }
    if (target === 'save') return t(limit === null ? 'plan.quota.length.save' : 'plan.quota.length.saveLimit', params)
    if (target === 'version') {
      return t(limit === null ? 'plan.quota.length.version' : 'plan.quota.length.versionLimit', params)
    }
    return t(limit === null ? 'plan.quota.length.new' : 'plan.quota.length.newLimit', params)
  }
  if (detail.quota === 'feature') {
    const key =
      typeof detail.feature === 'string' && Object.hasOwn(FEATURES, detail.feature)
        ? FEATURES[detail.feature]
        : undefined
    const params = { name: key === undefined ? '' : t(key), ways }
    const named = key !== undefined
    if (target === 'save') return t(named ? 'plan.quota.feature.saveNamed' : 'plan.quota.feature.save', params)
    if (target === 'version') {
      return t(named ? 'plan.quota.feature.versionNamed' : 'plan.quota.feature.version', params)
    }
    return t(named ? 'plan.quota.feature.newNamed' : 'plan.quota.feature.new', params)
  }
  if (detail.quota === 'saves') {
    // #577: Save and Update to MOWL together, per hour and per 24 hours (rolling).
    const limit = whole(detail.limit)
    const when = detail.window === 'day' ? 'day' : 'hour'
    const window = t(`plan.quota.saves.window.${when}`)
    const later = t(`plan.quota.saves.later.${when}`)
    return limit === null
      ? t('plan.quota.saves', { window, later })
      : t('plan.quota.savesLimit', { classes: t('plan.classes', { count: limit }), window, later })
  }
  const month = periodMonth(detail.period_end) ?? t('plan.thisMonth')
  const day = dayText(detail.period_end)
  const more = day === null ? t('plan.quota.moreNextMonth') : t('plan.quota.moreFrom', { day })
  if (detail.quota === 'cost') return t('plan.quota.cost', { month, more })
  const limit = whole(detail.limit)
  if (limit === null) return t('plan.quota.classesNoLimit', { month, more })
  const used = whole(detail.used)
  const classes = t('plan.classes', { count: limit })
  return used !== null && used !== limit
    ? t('plan.quota.classesUsed', { used, classes, month, more })
    : t('plan.quota.classesAll', { classes, month, more })
}

/** The sentence for a 429 `busy`, with the plan's limit when the server sent it. */
export function busyText(error: ApiError): string {
  const limit = whole(detailOf(error).limit)
  if (limit === null || limit < 1) return t('plan.busy.generic')
  return t('plan.busy.limit', { designs: t('plan.designs', { count: limit }) })
}

const ACCOUNT_REASONS: Record<string, CatalogueKey> = {
  code_invalid: 'plan.reason.code_invalid',
  already_redeemed: 'plan.reason.already_redeemed',
  trial_used: 'plan.reason.trial_used',
  trial_off: 'plan.reason.trial_off',
}

/** The sentence for a plan refusal, or null when the error is not one of them. */
export function planRefusalText(error: unknown): string | null {
  if (!(error instanceof ApiError)) return null
  if (error.reason === 'quota') return quotaText(error)
  if (error.reason === 'busy') return busyText(error)
  if (error.reason !== null && Object.hasOwn(ACCOUNT_REASONS, error.reason)) {
    return t(ACCOUNT_REASONS[error.reason]!)
  }
  return null
}

/** The words for a failed redeem or trial request on the account page. */
export function accountPlanError(error: unknown): string {
  return planRefusalText(error) ?? errorMessage(error)
}

const SOURCES: Record<string, CatalogueKey> = {
  grant: 'plan.source.grant',
  code: 'plan.source.code',
  trial: 'plan.source.trial',
  subscription: 'plan.source.subscription',
}

export function sourceText(source: string | null): string | null {
  return source !== null && Object.hasOwn(SOURCES, source) ? t(SOURCES[source]!) : null
}
