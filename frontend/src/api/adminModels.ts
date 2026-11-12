import { adminError } from './admin'
import { ApiError, get, request } from './client'
import { count, flag, list, object, strings, text, type JsonObject } from './decode'

// The admin area's Model routes (ADR 0012 section 5, ADR 0031 section 8;
// backend/src/classbeat/admin/models.py). Model names are shown here and nowhere a user sees.
// `set_route` refuses a model that has not passed the eval suite: 409 with a reason code and
// nothing changes.

const BASE = '/api/admin/models'

export const STAGE_GROUPS = ['design', 'songs'] as const
export type StageGroup = (typeof STAGE_GROUPS)[number]

export interface ModelPlan {
  id: string
  name: string
  rank: number
  routeKey: string
  /** The word users see for the plan: Standard, Better or Best (the API sends it in lower case). */
  quality: string
}

export interface ModelRoute {
  routeKey: string
  stageGroup: string
  provider: string
  model: string
  effort: string
  setAt: string
  setBy: string
  /** Why the result under the route is out of date (`newer_failure` means the worker refuses it). */
  stale: string[]
  refusedByWorker: boolean
}

export interface CatalogEntry {
  provider: string
  model: string
  efforts: string[]
  dataPolicy: string
  /** False when `set_route` would refuse it for its data policy or the DeepSeek switch. */
  enabled: boolean
}

export interface ModelsView {
  plans: ModelPlan[]
  routes: ModelRoute[]
  catalog: CatalogEntry[]
}

const QUALITY_WORDS: Record<string, string> = { standard: 'Standard', better: 'Better', best: 'Best' }

function decodePlan(value: unknown): ModelPlan {
  const item = object(value, 'plan')
  return {
    id: text(item, 'id'),
    name: text(item, 'name'),
    rank: count(item, 'rank'),
    routeKey: text(item, 'route_key'),
    quality: QUALITY_WORDS[text(item, 'quality')] ?? text(item, 'quality'),
  }
}

export function decodeRoute(value: unknown): ModelRoute {
  const item = object(value, 'route')
  return {
    routeKey: text(item, 'route_key'),
    stageGroup: text(item, 'stage_group'),
    provider: text(item, 'provider'),
    model: text(item, 'model'),
    effort: text(item, 'effort'),
    setAt: text(item, 'set_at'),
    setBy: text(item, 'set_by'),
    stale: strings(item, 'stale'),
    refusedByWorker: flag(item, 'refused_by_worker'),
  }
}

function decodeCatalog(value: unknown): CatalogEntry {
  const item: JsonObject = object(value, 'catalog entry')
  return {
    provider: text(item, 'provider'),
    model: text(item, 'model'),
    efforts: strings(item, 'efforts'),
    dataPolicy: text(item, 'data_policy'),
    enabled: flag(item, 'enabled'),
  }
}

export async function getModels(signal?: AbortSignal): Promise<ModelsView> {
  const body = object(await get(BASE, signal), 'models')
  return {
    plans: list(body, 'plans').map(decodePlan),
    routes: list(body, 'routes').map(decodeRoute),
    catalog: list(body, 'catalog').map(decodeCatalog),
  }
}

export interface RouteInput {
  provider: string
  model: string
  effort: string
}

/** `PUT /models/routes/{route_key}/{stage_group}`; the answer is the route as now stored. */
export async function setModelRoute(routeKey: string, stage: StageGroup, input: RouteInput): Promise<ModelRoute> {
  const path = `${BASE}/routes/${encodeURIComponent(routeKey)}/${stage}`
  return decodeRoute(await request('PUT', path, { body: { ...input } }))
}

// The refusal codes of `classbeat.models.routes.set_route`, in words.
const REFUSALS: Record<string, string> = {
  no_result: 'This model has not passed the eval suite at this effort. Run the eval suite for it first.',
  result_failed: 'The newest eval result for this model and effort failed.',
  suite_version: 'Its passing result is from an older version of the eval suite. Run the suite again.',
  manifest_digest: 'Its passing result was recorded against different eval cases. Run the suite again.',
  prompt_versions: 'Its passing result was recorded with other prompt versions. Run the suite again.',
  not_in_catalog: 'That model is not in the catalog.',
  effort_not_in_catalog: 'That effort is not offered for this model.',
  data_policy: 'The data policy of this provider does not allow it.',
  deepseek_disabled: 'DeepSeek is switched off on the server.',
  invalid_route_key: 'That route key is not valid.',
  invalid_stage_group: 'That stage is not valid.',
  invalid_actor: 'Your account could not be recorded for this change.',
  foreign_key: 'The eval result under this choice is gone. Run the eval suite again.',
}

/** True for the 409 `set_route` answers: the choice was refused and nothing changed. */
export function isRouteRefusal(error: unknown): boolean {
  return error instanceof ApiError && error.status === 409 && error.reason !== null && Object.hasOwn(REFUSALS, error.reason)
}

/** The words for a failed route write; a refusal says plainly that the route did not change. */
export function modelError(error: unknown): string {
  if (isRouteRefusal(error) && error instanceof ApiError && error.reason !== null) {
    return `Refused, the route is unchanged. ${REFUSALS[error.reason] ?? ''}`.trim()
  }
  return adminError(error)
}
