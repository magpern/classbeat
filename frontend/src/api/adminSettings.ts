import { del, get, request } from './client'
import { count, flag, isObject, list, object, optionalText, strings, text, type JsonObject } from './decode'

// The admin area's Settings, flags and plan limits (ADR 0011 sections 4, 6 and 7;
// backend/src/classbeat/admin/settings.py, AD4 #222). Every write needs a step-up; a refusal is
// 422 with a reason and changes nothing. Clearing a flag stores the JSON null, so the
// environment's value applies again. Server text (a banner) is data, rendered as text only.

const BASE = '/api/admin'

export type FlagKey = 'signup_mode' | 'song_picking' | 'swedebeat_enabled' | 'trial' | 'banner'

export interface Person {
  id: string
  email: string | null
}

export interface Trial {
  plan: string
  days: number
}

export interface BannerValue {
  text: string
  level: string
}

/** One flag as `GET /settings` shows it; values keep the API's JSON shape. */
export interface Flag {
  key: string
  /** `environment` or `database`. */
  source: string
  effective: unknown
  environment: unknown
  /** The stored value; null when there is none (or it was cleared). */
  stored: unknown
  storedValid: boolean
  updatedAt: string | null
  updatedBy: Person | null
}

export interface Plan {
  id: string
  name: string
  rank: number
  classesPerMonth: number
  revisionsPerClass: number
  concurrentJobs: number
  monthlyCostMicros: number
  maxClassMinutes: number
  gamesAllowed: boolean
  racesAllowed: boolean
  ftpTestsAllowed: boolean
  /** Prices are whole cents and exist in the admin area only (billing is off). */
  monthlyPriceCents: number | null
  introPriceCents: number | null
  /** A date, `YYYY-MM-DD`. */
  introPriceUntil: string | null
  currency: string | null
  /** The plan's own Save and Update caps (#809); null = the server's setting applies. */
  savesPerHour: number | null
  savesPerDay: number | null
  features: string[]
  updatedAt: string
  updatedBy: Person | null
}

export interface Bounds {
  classesPerMonth: number
  revisionsPerClass: number
  concurrentJobs: number
  monthlyCostMicros: number
  maxClassMinutes: number
  trialDays: number
  /** The server's Save and Update caps (CLASSBEAT_SAVES_PER_HOUR, _PER_DAY); a plan can only tighten them. */
  savesPerHour: number
  savesPerDay: number
}

export interface ReadOnly {
  agentJobCostMicros: number
  songJobCostMicros: number
  billingEnabled: boolean
}

export interface SettingsView {
  flags: Flag[]
  plans: Plan[]
  bounds: Bounds
  readOnly: ReadOnly
}

function optionalCount(item: JsonObject, key: string): number | null {
  return item[key] === null || item[key] === undefined ? null : count(item, key)
}

function decodePerson(value: unknown): Person | null {
  if (value === null || value === undefined) return null
  const item = object(value, 'person')
  return { id: text(item, 'id'), email: optionalText(item, 'email') }
}

export function decodeFlag(value: unknown): Flag {
  const item = object(value, 'flag')
  return {
    key: text(item, 'key'),
    source: text(item, 'source'),
    effective: item.effective ?? null,
    environment: item.environment ?? null,
    stored: item.stored ?? null,
    storedValid: flag(item, 'stored_valid'),
    updatedAt: optionalText(item, 'updated_at'),
    updatedBy: decodePerson(item.updated_by),
  }
}

export function decodePlan(value: unknown): Plan {
  const item = object(value, 'plan')
  return {
    id: text(item, 'id'),
    name: text(item, 'name'),
    rank: count(item, 'rank'),
    classesPerMonth: count(item, 'classes_per_month'),
    revisionsPerClass: count(item, 'revisions_per_class'),
    concurrentJobs: count(item, 'concurrent_jobs'),
    monthlyCostMicros: count(item, 'monthly_cost_micros'),
    maxClassMinutes: count(item, 'max_class_minutes'),
    gamesAllowed: flag(item, 'games_allowed'),
    racesAllowed: flag(item, 'races_allowed'),
    ftpTestsAllowed: flag(item, 'ftp_tests_allowed'),
    monthlyPriceCents: optionalCount(item, 'monthly_price_cents'),
    introPriceCents: optionalCount(item, 'intro_price_cents'),
    introPriceUntil: optionalText(item, 'intro_price_until'),
    currency: optionalText(item, 'currency'),
    savesPerHour: optionalCount(item, 'saves_per_hour'),
    savesPerDay: optionalCount(item, 'saves_per_day'),
    features: strings(item, 'features'),
    updatedAt: text(item, 'updated_at'),
    updatedBy: decodePerson(item.updated_by),
  }
}

export async function getSettings(signal?: AbortSignal): Promise<SettingsView> {
  const body = object(await get(`${BASE}/settings`, signal), 'settings')
  const bounds = object(body.bounds, 'bounds')
  const readOnly = object(body.read_only, 'read_only')
  return {
    flags: list(body, 'flags').map(decodeFlag),
    plans: list(body, 'plans').map(decodePlan),
    bounds: {
      classesPerMonth: count(bounds, 'classes_per_month'),
      revisionsPerClass: count(bounds, 'revisions_per_class'),
      concurrentJobs: count(bounds, 'concurrent_jobs'),
      monthlyCostMicros: count(bounds, 'monthly_cost_micros'),
      maxClassMinutes: count(bounds, 'max_class_minutes'),
      trialDays: count(bounds, 'trial_days'),
      savesPerHour: count(bounds, 'saves_per_hour'),
      savesPerDay: count(bounds, 'saves_per_day'),
    },
    readOnly: {
      agentJobCostMicros: count(readOnly, 'agent_job_cost_micros'),
      songJobCostMicros: count(readOnly, 'song_job_cost_micros'),
      billingEnabled: flag(readOnly, 'billing_enabled'),
    },
  }
}

function settingPath(key: string): string {
  return `${BASE}/settings/${encodeURIComponent(key)}`
}

/** Stores a flag (`PUT /settings/{key}` with `{value}`); the answer is the flag as now shown. */
export async function setFlag(key: FlagKey, value: string | Trial | BannerValue): Promise<Flag> {
  return decodeFlag(await request('PUT', settingPath(key), { body: { value } }))
}

/** Clears a flag (stored as JSON null): the environment's value applies again. */
export async function clearFlag(key: FlagKey): Promise<Flag> {
  return decodeFlag(await del(settingPath(key)))
}

export interface PlanLimits {
  classesPerMonth?: number
  revisionsPerClass?: number
  concurrentJobs?: number
  monthlyCostMicros?: number
  maxClassMinutes?: number
  gamesAllowed?: boolean
  racesAllowed?: boolean
  ftpTestsAllowed?: boolean
  /** A price: a number sets it, null clears it, undefined leaves it out of the request. */
  monthlyPriceCents?: number | null
  introPriceCents?: number | null
  introPriceUntil?: string | null
  currency?: string | null
  /** A save cap: a number sets it, null clears it (the server's setting applies), undefined leaves it out. */
  savesPerHour?: number | null
  savesPerDay?: number | null
}

/** Sends only the limits that changed (`PATCH /plans/{id}`). */
export async function updatePlan(id: string, limits: PlanLimits): Promise<Plan> {
  const body: JsonObject = {}
  if (limits.classesPerMonth !== undefined) body.classes_per_month = limits.classesPerMonth
  if (limits.revisionsPerClass !== undefined) body.revisions_per_class = limits.revisionsPerClass
  if (limits.concurrentJobs !== undefined) body.concurrent_jobs = limits.concurrentJobs
  if (limits.monthlyCostMicros !== undefined) body.monthly_cost_micros = limits.monthlyCostMicros
  if (limits.maxClassMinutes !== undefined) body.max_class_minutes = limits.maxClassMinutes
  if (limits.gamesAllowed !== undefined) body.games_allowed = limits.gamesAllowed
  if (limits.racesAllowed !== undefined) body.races_allowed = limits.racesAllowed
  if (limits.ftpTestsAllowed !== undefined) body.ftp_tests_allowed = limits.ftpTestsAllowed
  if (limits.monthlyPriceCents !== undefined) body.monthly_price_cents = limits.monthlyPriceCents
  if (limits.introPriceCents !== undefined) body.intro_price_cents = limits.introPriceCents
  if (limits.introPriceUntil !== undefined) body.intro_price_until = limits.introPriceUntil
  if (limits.currency !== undefined) body.currency = limits.currency
  if (limits.savesPerHour !== undefined) body.saves_per_hour = limits.savesPerHour
  if (limits.savesPerDay !== undefined) body.saves_per_day = limits.savesPerDay
  return decodePlan(await request('PATCH', `${BASE}/plans/${encodeURIComponent(id)}`, { body }))
}

// --- reading the JSON values -------------------------------------------------------------------

export function asText(value: unknown): string | null {
  return typeof value === 'string' ? value : null
}

export function asTrial(value: unknown): Trial | null {
  if (!isObject(value)) return null
  const { plan, days } = value
  return typeof plan === 'string' && typeof days === 'number' ? { plan, days } : null
}

export function asBanner(value: unknown): BannerValue | null {
  if (!isObject(value)) return null
  const { text: words, level } = value
  return typeof words === 'string' && typeof level === 'string' ? { text: words, level } : null
}
