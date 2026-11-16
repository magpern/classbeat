import { ApiError, get, post } from './client'
import { count, DecodeError, isObject, list, object, optionalText, strings, text } from './decode'
import { OUTPUTS, type Output } from './studioOutputs'

// The per-output states of a `produce_outputs` approval and the actions that are safe for each
// (ADR 0036 section 6, ADR 0005 section 7; backend `classes/studio_output_actions*.py`). The API
// sends no key, reference, provider id or URL; only codes, the stored name and a found MOWL entry.

const classBase = (classId: string) => `/api/classes/${encodeURIComponent(classId)}`

export type OutputState = 'pending' | 'running' | 'done' | 'failed' | 'in_doubt' | 'published_not_listed'
export type OutputAction = 'check_again' | 'confirm' | 'create_another' | 'retry'
export type OutputNote = 'created_not_public' | 'may_exist' | 'listed_candidate' | 'not_sent'

const STATES: readonly string[] = ['pending', 'running', 'done', 'failed', 'in_doubt', 'published_not_listed']
const ACTIONS: readonly string[] = ['check_again', 'confirm', 'create_another', 'retry']
const NOTES: readonly string[] = ['created_not_public', 'may_exist', 'listed_candidate', 'not_sent']

export interface OutputStatus {
  revisionId: string
  output: Output
  attempt: number
  state: OutputState
  actions: OutputAction[]
  note: OutputNote | null
  /** The stored name of what was created (a playlist or mix), when there is one. */
  name: string | null
  /** The failure's category (`writes_off`, `rate_limited` ...), for a failed output. */
  category: string | null
  /** "Create another anyway" was used: the next approval makes a new attempt. */
  superseded: boolean
  /** A MOWL entry found by the mix's unique name, shown for the user's own decision. */
  candidate: { name: string; durationSec: number | null } | null
}

export interface OutputsView {
  /** The outputs this environment runs for the user, in order; the page offers only these. */
  enabled: Output[]
  outputs: OutputStatus[]
}

function oneOf<T extends string>(value: string, allowed: readonly string[], what: string): T {
  if (!allowed.includes(value)) throw new DecodeError(what)
  return value as T
}

export function decodeOutputStatus(value: unknown): OutputStatus {
  const item = object(value, 'output')
  const candidate = isObject(item.candidate) ? item.candidate : null
  const note = optionalText(item, 'note')
  const duration = candidate?.duration_sec
  return {
    revisionId: text(item, 'revision_id'),
    output: oneOf<Output>(text(item, 'output'), OUTPUTS, 'output'),
    attempt: count(item, 'attempt'),
    state: oneOf<OutputState>(text(item, 'state'), STATES, 'state'),
    actions: strings(item, 'actions').filter((a): a is OutputAction => ACTIONS.includes(a)),
    note: note !== null && NOTES.includes(note) ? (note as OutputNote) : null,
    name: optionalText(item, 'name'),
    category: optionalText(item, 'category'),
    superseded: item.superseded === true,
    candidate:
      candidate !== null && typeof candidate.name === 'string'
        ? { name: candidate.name, durationSec: typeof duration === 'number' ? duration : null }
        : null,
  }
}

export function decodeOutputsView(value: unknown): OutputsView {
  const item = object(value, 'outputs')
  return {
    enabled: strings(item, 'enabled_outputs').filter((o): o is Output => OUTPUTS.includes(o as Output)),
    outputs: list(item, 'outputs').map(decodeOutputStatus),
  }
}

/** `null` for a class the server does not know or with the studio off (404). */
export async function getOutputs(classId: string, signal?: AbortSignal): Promise<OutputsView | null> {
  try {
    return decodeOutputsView(await get(`${classBase(classId)}/outputs`, signal))
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return null
    throw error
  }
}

const actionPath = (classId: string, output: Output, action: string) =>
  `${classBase(classId)}/outputs/${output}/${action}`

/** "Check again": reconcile the output once more. Safe to repeat; it never creates anything. */
export async function checkAgain(classId: string, revisionId: string, output: Output, key: string): Promise<OutputStatus> {
  return decodeOutputStatus(await post(actionPath(classId, output, 'check-again'), { revision_id: revisionId }, { 'Idempotency-Key': key }))
}

/** The user's decision that the MOWL entry shown is their mix. */
export async function confirmMix(classId: string, revisionId: string, key: string): Promise<OutputStatus> {
  return decodeOutputStatus(await post(actionPath(classId, 'swedebeat_mix', 'confirm'), { revision_id: revisionId }, { 'Idempotency-Key': key }))
}

/**
 * "Create another anyway" (ADR 0005's Do it anyway): only after the user has accepted the
 * duplicate risk in words. The next approval of the outputs then makes a new attempt.
 */
export async function createAnotherAnyway(classId: string, revisionId: string, output: Output, key: string): Promise<OutputStatus> {
  const body = { revision_id: revisionId, acknowledge_duplicate: true }
  return decodeOutputStatus(await post(actionPath(classId, output, 'create-another'), body, { 'Idempotency-Key': key }))
}
