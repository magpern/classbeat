import type { RuleHit } from '../api/classes'
import { clock } from './workout'
import { decimal } from '../i18n/format'
import { t } from '../i18n/t'

/**
 * The words of the pattern notes (ADR 0017 section 4), keyed by rule id. The sentences mirror the
 * backend's `RATIONALES` through the rule-to-rationale link of the profiles; a test reads the
 * backend files and fails when a rule id or a sentence is missing or differs. They say what is
 * usual, never that the class is wrong.
 */
const RULE_IDS = [
  'warmup_short',
  'hard_start_late',
  'cooldown_short',
  'cooldown_long',
  'hard_share_low',
  'hard_share_high',
  'zone_ceiling_exceeded',
  'top_zone_share_low',
  'top_zone_share_high',
  'mean_zone_low',
  'mean_zone_high',
  'tss_rate_low',
  'tss_rate_high',
  'hard_too_early',
  'standing_long',
  'longest_interval_short',
  'longest_interval_long',
  'median_interval_short',
  'median_interval_long',
  'sprints_few',
  'sprints_many',
  'climb_share_low',
  'climb_share_high',
  'work_rest_low',
  'work_rest_high',
  'intensity_factor_low',
  'intensity_factor_high',
] as const

/**
 * The sentence of each rule, read through `t` each time so it follows the active language.
 * Getters keep the shape of a plain record (keys, values and lookups) for callers and the test.
 */
export const RULE_COPY: Readonly<Record<string, string>> = Object.defineProperties(
  {},
  Object.fromEntries(
    RULE_IDS.map((id) => [id, { enumerable: true, get: () => t(`class.rule.${id}`) }]),
  ),
)

/** At most this many notes are shown (the backend caps the same). */
export const MAX_NOTES = 5

function amount(metric: string, value: number): string {
  if (metric.endsWith('_s')) return clock(value)
  if (metric.endsWith('_share') || metric.includes('_share_')) return `${String(Math.round(value * 100))}%`
  if (metric === 'sprint_count') return String(Math.round(value))
  if (metric === 'intensity_factor' || metric === 'work_rest_ratio') return decimal(value, 2)
  return decimal(value, 1)
}

/** "Here 4:00, usual 6:10 or more" / "up to 17:24" / "between a and b". */
export function valueText(hit: Pick<RuleHit, 'metric' | 'value' | 'bounds'>): string {
  const [low, high] = hit.bounds
  const here = amount(hit.metric, hit.value)
  const withUnit = (text: string): string =>
    hit.metric === 'tss_per_minute' ? t('class.notes.perMinute', { value: text }) : text
  let usual: string
  if (low !== null && high !== null) {
    usual = t('class.notes.between', { low: amount(hit.metric, low), high: amount(hit.metric, high) })
  } else if (low !== null) {
    usual = t('class.notes.orMore', { low: amount(hit.metric, low) })
  } else if (high !== null) {
    usual = t('class.notes.upTo', { high: amount(hit.metric, high) })
  } else {
    return t('class.notes.hereOnly', { here: withUnit(here) })
  }
  return t('class.notes.hereUsual', { here: withUnit(here), usual: withUnit(usual) })
}
