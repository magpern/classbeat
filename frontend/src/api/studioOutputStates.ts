import type { Output } from './studioOutputs'

// SEAM for C7 (#1113, not merged): the per-output states of a `produce_outputs` approval and the
// actions that are safe for each (ADR 0036 section 6, "Per-output states"). Only the types are
// here, so part 3 can fill them in; no route is named or called, and nothing is shown.
// TODO(#1113): the read of `class_outputs`, "Check again" (ADR 0005's reconciler) and "Create
// another anyway" (ADR 0005's Do it anyway: `attempt + 1`, after the duplicate-risk warning).

export type OutputState = 'running' | 'done' | 'failed' | 'in_doubt'

export interface OutputStatus {
  output: Output
  attempt: number
  state: OutputState
}

export interface OutputActions {
  checkAgain: (output: Output) => Promise<void>
  createAnotherAnyway: (output: Output) => Promise<void>
}
