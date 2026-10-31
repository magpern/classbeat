import { get } from './client'
import { count, list, object, optionalText, text, type JsonObject } from './decode'

// The admin area's read-only views (ADR 0011 section 6): Usage and cost and Jobs
// (backend/src/classbeat/admin/usage.py and jobs.py, #221) and the audit log
// (backend/src/classbeat/admin/audit_routes.py, #222). Kept apart from `admin.ts` (Users and
// Codes) to keep both files small. Server text is data, rendered as text only.

const BASE = '/api/admin'

// --- usage and cost ----------------------------------------------------------------------------

export interface Sums {
  runs: number
  jobs: number
  inputTokens: number
  outputTokens: number
  cacheReadTokens: number
  cacheWriteTokens: number
  costMicros: number
}

export interface Totals extends Sums {
  classes: number
  revisions: number
  saves: number
}

export interface PlanUsage {
  /** null: runs of jobs without a credit ledger row (revisions, saves, older jobs). */
  planId: string | null
  classes: number
  reserved: number
  committed: number
  released: number
  refunded: number
  sums: Sums
}

export interface KindModelUsage {
  jobKind: string
  provider: string
  model: string
  sums: Sums
}

export interface TopUser {
  userId: string
  email: string | null
  runs: number
  costMicros: number
  classes: number
}

export interface Rates {
  input: string
  output: string
  cacheWrite: string
  cacheRead: string
}

export interface PriceRow {
  provider: string
  model: string
  rates: Rates
  aboveTokens: number
  above: Rates | null
}

export interface Usage {
  month: string
  totals: Totals
  byPlan: PlanUsage[]
  byKindModel: KindModelUsage[]
  jobsByKind: { kind: string; completed: number }[]
  topUsers: TopUser[]
  priceVersions: { priceVersion: string; runs: number }[]
  pricesVersion: string
  prices: PriceRow[]
}

function decodeSums(value: unknown): Sums {
  const item = object(value, 'sums')
  return {
    runs: count(item, 'runs'),
    jobs: count(item, 'jobs'),
    inputTokens: count(item, 'input_tokens'),
    outputTokens: count(item, 'output_tokens'),
    cacheReadTokens: count(item, 'cache_read_tokens'),
    cacheWriteTokens: count(item, 'cache_write_tokens'),
    costMicros: count(item, 'cost_micros'),
  }
}

function decodeRates(value: unknown): Rates {
  const item = object(value, 'rates')
  return {
    input: text(item, 'input'),
    output: text(item, 'output'),
    cacheWrite: text(item, 'cache_write'),
    cacheRead: text(item, 'cache_read'),
  }
}

function decodeUsage(value: unknown): Usage {
  const body = object(value, 'usage')
  const totals = object(body.totals, 'totals')
  return {
    month: text(body, 'month'),
    totals: {
      ...decodeSums(totals),
      classes: count(totals, 'classes'),
      revisions: count(totals, 'revisions'),
      saves: count(totals, 'saves'),
    },
    byPlan: list(body, 'by_plan').map((row) => {
      const item = object(row, 'plan usage')
      return {
        planId: optionalText(item, 'plan_id'),
        classes: count(item, 'classes'),
        reserved: count(item, 'reserved'),
        committed: count(item, 'committed'),
        released: count(item, 'released'),
        refunded: count(item, 'refunded'),
        sums: decodeSums(item.sums),
      }
    }),
    byKindModel: list(body, 'by_kind_model').map((row) => {
      const item = object(row, 'kind usage')
      return {
        jobKind: text(item, 'job_kind'),
        provider: text(item, 'provider'),
        model: text(item, 'model'),
        sums: decodeSums(item.sums),
      }
    }),
    jobsByKind: list(body, 'jobs_by_kind').map((row) => {
      const item = object(row, 'kind count')
      return { kind: text(item, 'kind'), completed: count(item, 'completed') }
    }),
    topUsers: list(body, 'top_users').map((row) => {
      const item = object(row, 'top user')
      return {
        userId: text(item, 'user_id'),
        email: optionalText(item, 'email'),
        runs: count(item, 'runs'),
        costMicros: count(item, 'cost_micros'),
        classes: count(item, 'classes'),
      }
    }),
    priceVersions: list(body, 'price_versions').map((row) => {
      const item = object(row, 'price version')
      return { priceVersion: text(item, 'price_version'), runs: count(item, 'runs') }
    }),
    pricesVersion: text(body, 'prices_version'),
    prices: list(body, 'prices').map((row) => {
      const item = object(row, 'price')
      return {
        provider: text(item, 'provider'),
        model: text(item, 'model'),
        rates: decodeRates(item.rates),
        aboveTokens: count(item, 'above_tokens'),
        above: item.above === null || item.above === undefined ? null : decodeRates(item.above),
      }
    }),
  }
}

export const MONTH = /^\d{4}-(0[1-9]|1[0-2])$/

export function usagePath(month: string | null): string {
  return month === null ? `${BASE}/usage` : `${BASE}/usage?month=${month}`
}

export async function getUsage(month: string | null, signal?: AbortSignal): Promise<Usage> {
  return decodeUsage(await get(usagePath(month), signal))
}

// --- jobs --------------------------------------------------------------------------------------

export interface Jobs {
  days: number
  states: { state: string; count: number }[]
  waitingApprovals: {
    userId: string
    email: string | null
    kind: string
    waitingSince: string
    expiresAt: string | null
  }[]
  failures: { kind: string; category: string; count: number; lastAt: string }[]
  uncertainOperations: {
    userId: string
    email: string | null
    kind: string
    since: string
    jobState: string | null
  }[]
}

export const JOB_WINDOWS = [7, 30, 90] as const

export function jobsPath(days: number): string {
  return `${BASE}/jobs?days=${String(days)}`
}

export async function getJobs(days: number, signal?: AbortSignal): Promise<Jobs> {
  const body = object(await get(jobsPath(days), signal), 'jobs')
  return {
    days: count(body, 'days'),
    states: list(body, 'states').map((row) => {
      const item = object(row, 'state')
      return { state: text(item, 'state'), count: count(item, 'count') }
    }),
    waitingApprovals: list(body, 'waiting_approvals').map((row) => {
      const item = object(row, 'waiting approval')
      return {
        userId: text(item, 'user_id'),
        email: optionalText(item, 'email'),
        kind: text(item, 'kind'),
        waitingSince: text(item, 'waiting_since'),
        expiresAt: optionalText(item, 'expires_at'),
      }
    }),
    failures: list(body, 'failures').map((row) => {
      const item = object(row, 'failure')
      return {
        kind: text(item, 'kind'),
        category: text(item, 'category'),
        count: count(item, 'count'),
        lastAt: text(item, 'last_at'),
      }
    }),
    uncertainOperations: list(body, 'uncertain_operations').map((row) => {
      const item = object(row, 'uncertain operation')
      return {
        userId: text(item, 'user_id'),
        email: optionalText(item, 'email'),
        kind: text(item, 'kind'),
        since: text(item, 'since'),
        jobState: optionalText(item, 'job_state'),
      }
    }),
  }
}

// --- audit log ---------------------------------------------------------------------------------

/** The audit log's filters as they stand in the page's address (dates as `YYYY-MM-DD`). */
export interface AuditFilter {
  action: string
  outcome: string
  actorId: string
  targetUserId: string
  targetKind: string
  targetId: string
  since: string
  until: string
}

export const EMPTY_FILTER: AuditFilter = {
  action: '',
  outcome: '',
  actorId: '',
  targetUserId: '',
  targetKind: '',
  targetId: '',
  since: '',
  until: '',
}

// Filter field -> its name in the page's address and in the API request.
const FILTER_KEYS: Record<keyof AuditFilter, string> = {
  action: 'action',
  outcome: 'outcome',
  actorId: 'actor_id',
  targetUserId: 'target_user_id',
  targetKind: 'target_kind',
  targetId: 'target_id',
  since: 'since',
  until: 'until',
}

const DAY = /^\d{4}-\d{2}-\d{2}$/

/** The filter from the page's address; unknown or malformed values are dropped. */
export function filterFromSearch(search: URLSearchParams): AuditFilter {
  const filter = { ...EMPTY_FILTER }
  for (const [field, key] of Object.entries(FILTER_KEYS) as [keyof AuditFilter, string][]) {
    const value = (search.get(key) ?? '').trim()
    if ((field === 'since' || field === 'until') && !DAY.test(value)) continue
    filter[field] = value.slice(0, 100)
  }
  return filter
}

/** The page's address for a filter: only the fields that are set, in a fixed order. */
export function filterSearch(filter: AuditFilter): string {
  const params = new URLSearchParams()
  for (const [field, key] of Object.entries(FILTER_KEYS) as [keyof AuditFilter, string][]) {
    const value = filter[field].trim()
    if (value !== '') params.set(key, value)
  }
  const query = params.toString()
  return query === '' ? '' : `?${query}`
}

/** A `YYYY-MM-DD` day as the start of that day in the admin's time zone (ISO 8601). */
export function startOfDay(day: string, plusDays = 0): string | null {
  if (!DAY.test(day)) return null
  const when = new Date(`${day}T00:00:00`)
  if (Number.isNaN(when.getTime())) return null
  when.setDate(when.getDate() + plusDays)
  return when.toISOString()
}

export const AUDIT_PAGE = 50

/**
 * The API path for a filter, an optional actor email (never in the page's address) and a
 * cursor. `until` is a whole day: the request ends at the start of the next day.
 */
export function auditPath(filter: AuditFilter, actorEmail: string, before: string | null): string {
  const params = new URLSearchParams()
  for (const [field, key] of Object.entries(FILTER_KEYS) as [keyof AuditFilter, string][]) {
    const value = filter[field].trim()
    if (value === '') continue
    if (field === 'since') {
      const since = startOfDay(value)
      if (since !== null) params.set(key, since)
    } else if (field === 'until') {
      const until = startOfDay(value, 1)
      if (until !== null) params.set(key, until)
    } else {
      params.set(key, value)
    }
  }
  if (actorEmail.trim() !== '') params.set('actor_email', actorEmail.trim())
  if (before !== null) params.set('before', before)
  params.set('limit', String(AUDIT_PAGE))
  return `${BASE}/audit?${params.toString()}`
}

export interface AuditRow {
  id: string
  createdAt: string
  actorId: string | null
  actorEmail: string
  action: string
  targetUserId: string | null
  targetKind: string | null
  targetId: string | null
  params: JsonObject
  outcome: string
  reason: string | null
}

export async function listAudit(
  filter: AuditFilter,
  actorEmail: string,
  before: string | null,
  signal?: AbortSignal,
): Promise<{ rows: AuditRow[]; nextBefore: string | null }> {
  const body = object(await get(auditPath(filter, actorEmail, before), signal), 'audit')
  return {
    rows: list(body, 'rows').map((row) => {
      const item = object(row, 'audit row')
      return {
        id: text(item, 'id'),
        createdAt: text(item, 'created_at'),
        actorId: optionalText(item, 'actor_id'),
        actorEmail: text(item, 'actor_email'),
        action: text(item, 'action'),
        targetUserId: optionalText(item, 'target_user_id'),
        targetKind: optionalText(item, 'target_kind'),
        targetId: optionalText(item, 'target_id'),
        params: object(item.params, 'params'),
        outcome: text(item, 'outcome'),
        reason: optionalText(item, 'reason'),
      }
    }),
    nextBefore: optionalText(body, 'next_before'),
  }
}
