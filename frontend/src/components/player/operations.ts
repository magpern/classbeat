import type { Operation } from '../../api/jobs'
import { PLAYER_REMOVE_OPERATION, PLAYER_SEND_OPERATION } from '../../api/playerSend'

// Which of the account's undecided operations a panel shows (ADR 0034 sections 5 and 6).

/** The player's undecided send of class `classId`. */
export const sendOf = (classId: string) => (operation: Operation) =>
  operation.kind === PLAYER_SEND_OPERATION && operation.classId === classId

/** A removal made by deleting a class: it has no class, so the account page shows it. */
export const removal = (operation: Operation) => operation.kind === PLAYER_REMOVE_OPERATION
