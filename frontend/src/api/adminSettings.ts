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
  features: string[]
  updatedAt: string
  updatedBy: Person | null
}

export interface Bounds {
  classesPerMonth: number
  revisionsPerClass: number
  concurrentJobs: number
  monthlyCostMicros: number
  trialDays: number
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
      trialDays: count(bounds, 'trial_days'),
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
}

/** Sends only the limits that changed (`PATCH /plans/{id}`). */
export async function updatePlan(id: string, limits: PlanLimits): Promise<Plan> {
  const body: JsonObject = {}
  if (limits.classesPerMonth !== undefined) body.classes_per_month = limits.classesPerMonth
  if (limits.revisionsPerClass !== undefined) body.revisions_per_class = limits.revisionsPerClass
  if (limits.concurrentJobs !== undefined) body.concurrent_jobs = limits.concurrentJobs
  if (limits.monthlyCostMicros !== undefined) body.monthly_cost_micros = limits.monthlyCostMicros
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
