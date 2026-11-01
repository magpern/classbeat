import type { RuleHit } from '../api/classes'
import { clock } from './workout'

/**
 * The words of the pattern notes (ADR 0017 section 4), keyed by rule id. The sentences mirror the
 * backend's `RATIONALES` through the rule-to-rationale link of the profiles; a test reads the
 * backend files and fails when a rule id or a sentence is missing or differs. They say what is
 * usual, never that the class is wrong.
 */
export const RULE_COPY: Record<string, string> = {
  warmup_short: 'Most classes of this kind ride a few minutes before the first hard effort.',
  hard_start_late: 'Most classes of this kind reach the first hard effort before this point.',
  cooldown_short: 'Most classes of this kind end with an easy cool-down of a couple of minutes.',
  cooldown_long: 'Most classes of this kind keep the cool-down short.',
  hard_share_low: 'Most classes of this kind spend a good part of the ride in hard zones.',
  hard_share_high: 'Most classes of this kind leave room for easier riding between efforts.',
  zone_ceiling_exceeded: 'Most classes of this kind stay out of the highest zones.',
  top_zone_share_low: 'Most classes of this kind include some riding in the top zones.',
  top_zone_share_high: 'Most classes of this kind keep the top zones to a limited share.',
  mean_zone_low: 'Most classes of this kind have a steadier overall effort level than this.',
  mean_zone_high: 'Most classes of this kind have a lower overall effort level than this.',
  tss_rate_low: 'Most classes of this kind put a bit more training load into each minute.',
  tss_rate_high: 'Most classes of this kind put a bit less training load into each minute.',
  hard_too_early: 'Most classes of this kind hold the hard efforts back in the first quarter.',
  standing_long: 'Most classes of this kind keep standing to a limited share of the ride.',
}

/** At most this many notes are shown (the backend caps the same). */
export const MAX_NOTES = 5

export const NOTES_USE_CURRENT_RULES = 'Notes use the current rules'

export const EXPLAINED_REASON = 'Your brief asks for this, so it is only a note.'

function amount(metric: string, value: number): string {
  if (metric.endsWith('_s')) return clock(value)
  if (metric.endsWith('_share') || metric.includes('_share_')) return `${String(Math.round(value * 100))}%`
  if (metric === 'tss_per_minute') return value.toFixed(1)
  return value.toFixed(1)
}

/** "Here 4:00, usual 6:10 or more" / "up to 17:24" / "between a and b". */
export function valueText(hit: Pick<RuleHit, 'metric' | 'value' | 'bounds'>): string {
  const [low, high] = hit.bounds
  const here = amount(hit.metric, hit.value)
  const unit = hit.metric === 'tss_per_minute' ? ' TSS per minute' : ''
  let usual: string
  if (low !== null && high !== null) {
    usual = `${amount(hit.metric, low)} to ${amount(hit.metric, high)}`
  } else if (low !== null) {
    usual = `${amount(hit.metric, low)} or more`
  } else if (high !== null) {
    usual = `up to ${amount(hit.metric, high)}`
  } else {
    return `This one: ${here}${unit}`
  }
  return `This one: ${here}${unit}. Usual: ${usual}${unit}`
}
