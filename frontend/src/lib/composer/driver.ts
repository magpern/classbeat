import type { SavePending } from '../../api/composerState'
import type { PendingMessage } from '../../api/pendingMessages'

// The web's one driver (ADR 0038 section 8). Pure: the next step from what the class says now.
// When the class has no queued or running job it does the first of:
//
//   1. a sketch is current with no fill started for it: submit a pending message (a sketch revise,
//      so a shape change made while the sketch job ran is applied before songs are chosen), else
//      start the fill;
//   2. `save_pending` is set: save exactly that revision and hash;
//   3. a pending message exists: submit it (the server's state table decides what it becomes).
//
// A job waiting for approval does not count as running: a submit replaces it. A failed save is
// not retried (the server clears `save_pending` when the save ends, so rule 2 does not fire again),
// and a stored fill refusal is not retried (the fill is "started"); only the user's Find songs
// again does that. The caller fires each step at most once per `stepKey`.

export interface DriverInput {
  /** A job of the class is queued or running. */
  running: boolean
  /** The current revision is a sketch. */
  sketchCurrent: boolean
  /** A fill has been started for this very sketch (`composer.fill.sketchRevisionId`). */
  fillStarted: boolean
  savePending: SavePending | null
  /** The class's stored messages; only a `pending` one counts. */
  messages: readonly PendingMessage[]
}

export type DriverStep =
  | { kind: 'none' }
  | { kind: 'fill' }
  | { kind: 'submit'; messageId: string }
  | { kind: 'save'; target: SavePending }

export const IDLE: DriverStep = { kind: 'none' }

export function pendingOf(messages: readonly PendingMessage[]): PendingMessage | null {
  return messages.find((m) => m.status === 'pending') ?? null
}

export function nextStep(input: DriverInput): DriverStep {
  if (input.running) return IDLE
  const message = pendingOf(input.messages)
  if (input.sketchCurrent && !input.fillStarted) {
    return message !== null ? { kind: 'submit', messageId: message.messageId } : { kind: 'fill' }
  }
  if (input.savePending !== null) return { kind: 'save', target: input.savePending }
  if (message !== null) return { kind: 'submit', messageId: message.messageId }
  return IDLE
}

/** A string that changes when the step does, so the caller runs a step once per state. */
export function stepKey(classId: string, step: DriverStep, currentRevisionId: string | null): string {
  switch (step.kind) {
    case 'none':
      return 'none'
    case 'fill':
      return `fill:${classId}:${currentRevisionId ?? ''}`
    case 'submit':
      return `submit:${classId}:${step.messageId}`
    case 'save':
      return `save:${classId}:${step.target.jobId}:${step.target.revisionId}:${step.target.contentHash}`
  }
}
