import { get } from './client'
import { count, list, object, text, type JsonObject } from './decode'

// The user's monthly usage (ADR 0007 section 10, backend/src/classbeat/usage/routes.py).
// Server text (kind, model) is data and is rendered as text only.

export interface Sums {
  runs: number
  jobs: number
  inputTokens: number
  outputTokens: number
  cacheReadTokens: number
  cacheWriteTokens: number
  costMicros: number
}

export interface KindModelUsage extends Sums {
  jobKind: string
  model: string
}

export interface Usage {
  month: string
  total: Sums
  byKindModel: KindModelUsage[]
  classesDesigned: number
  revisions: number
  /** Codes for what the figure leaves out; the page words them. */
  notes: string[]
}

function sums(item: JsonObject): Sums {
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

export async function getUsage(signal?: AbortSignal): Promise<Usage> {
  const body = object(await get('/api/usage', signal), 'usage')
  return {
    month: text(body, 'month'),
    total: sums(object(body.total, 'total')),
    byKindModel: list(body, 'by_kind_model').map((entry) => {
      const item = object(entry, 'by_kind_model')
      return { ...sums(item), jobKind: text(item, 'job_kind'), model: text(item, 'model') }
    }),
    classesDesigned: count(body, 'classes_designed'),
    revisions: count(body, 'revisions'),
    notes: list(body, 'notes').map((n) => (typeof n === 'string' ? n : '')),
  }
}
