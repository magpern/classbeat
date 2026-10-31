import { get, post } from './client'
import { count, isObject, object, optionalText, strings, text } from './decode'

// The account plan API (ADR 0011 section 9, backend/src/classbeat/plans/routes.py) and the public
// settings. Server text is data: it is decoded into typed fields and rendered as text only.

export interface Limits {
  classesPerMonth: number
  revisionsPerClass: number
  concurrentJobs: number
}

export interface TrialOffer {
  plan: string
  days: number
}

export interface Plan {
  plan: string
  name: string
  /** `grant`, `code`, `trial` or `subscription`; null for the free plan without an entitlement. */
  source: string | null
  endsAt: string | null
  limits: Limits
  classesHeld: number
  periodEnd: string
  /** This period's model cost as a share of the cap, 0 to 1; never a dollar figure. */
  costShare: number
  features: string[]
  trial: TrialOffer | null
}

export function decodePlan(value: unknown): Plan {
  const item = object(value, 'plan')
  const limits = object(item.limits, 'limits')
  const trial = item.trial === null || item.trial === undefined ? null : object(item.trial, 'trial')
  const share = item.cost_share
  return {
    plan: text(item, 'plan'),
    name: text(item, 'name'),
    source: optionalText(item, 'source'),
    endsAt: optionalText(item, 'ends_at'),
    limits: {
      classesPerMonth: count(limits, 'classes_per_month'),
      revisionsPerClass: count(limits, 'revisions_per_class'),
      concurrentJobs: count(limits, 'concurrent_jobs'),
    },
    classesHeld: count(item, 'classes_held'),
    periodEnd: text(item, 'period_end'),
    costShare: typeof share === 'number' && Number.isFinite(share) ? Math.min(1, Math.max(0, share)) : 0,
    features: strings(item, 'features'),
    trial: trial === null ? null : { plan: text(trial, 'plan'), days: count(trial, 'days') },
  }
}

export async function getPlan(signal?: AbortSignal): Promise<Plan> {
  return decodePlan(await get('/api/account/plan', signal))
}

/** Redeems a code (400 `code_invalid`, 409 `already_redeemed`, 429 when rate limited). */
export async function redeemCode(code: string): Promise<void> {
  await post('/api/account/redeem', { code })
}

/** Starts the trial (409 `trial_off` or `trial_used`). */
export async function startTrial(): Promise<void> {
  await post('/api/account/trial')
}

export type BannerLevel = 'info' | 'warning'

export interface Banner {
  text: string
  level: BannerLevel
}

/** `GET /api/settings/public` (no auth): the banner, or null when there is none to show. */
export function decodeBanner(value: unknown): Banner | null {
  if (!isObject(value) || !isObject(value.banner)) return null
  const body = value.banner.text
  if (typeof body !== 'string' || body.trim() === '') return null
  return { text: body, level: value.banner.level === 'warning' ? 'warning' : 'info' }
}

export async function getBanner(signal?: AbortSignal): Promise<Banner | null> {
  return decodeBanner(await get('/api/settings/public', signal))
}
