import { ApiError, post } from "./client";
import { object, text } from "./decode";
import { errorMessage } from "./messages";
import { planRefusalText } from "./planMessages";

// Duplicate a class (ADR 0007 amendment of 2026-11-03, part D; backend classbeat/classes/duplicate.py).
// The browser names a version by id and the hash of its content; the server copies it.

export const TITLE_MAX = 100;

export interface Duplicated {
  classId: string;
  revisionId: string;
}

export async function duplicateClass(
  classId: string,
  revisionId: string,
  contentHash: string,
  title: string,
  /** Made once per dialog: a retried request makes no second class and takes no second credit. */
  idempotencyKey: string,
): Promise<Duplicated> {
  const body = object(
    await post(
      `/api/classes/${encodeURIComponent(classId)}/duplicate`,
      {
        revision_id: revisionId,
        content_hash: contentHash,
        title,
      },
      { "Idempotency-Key": idempotencyKey },
    ),
    "duplicated",
  );
  return {
    classId: text(body, "class_id"),
    revisionId: text(body, "revision_id"),
  };
}

/** The name the dialog proposes: the title with " (copy)", cut to the server's limit. */
export function copyTitle(title: string): string {
  const suffix = " (copy)";
  return `${title.slice(0, TITLE_MAX - suffix.length).trimEnd()}${suffix}`;
}

const REASONS: Record<string, string> = {
  not_ready:
    "This class has no finished design yet, so there is nothing to copy. Try again when it is ready.",
  feature_off:
    "Classes like this one cannot be made right now, so it cannot be copied.",
  soundless_not_evaluated:
    "Classes without music are not available on your plan yet, so this one cannot be copied.",
  not_changeable:
    "This class has no music of its own, so it cannot be changed by chat. You can still view, use and save its versions.",
  hash_mismatch:
    "That version is not the one this page showed: it was changed elsewhere. Reload the page and try again.",
  not_found: "This class, or that version, no longer exists.",
  invalid_input:
    "That name cannot be used. Give the copy a name of 1 to 100 characters.",
  idempotency_mismatch:
    "This copy was already asked for with other details. Close this and try again.",
  internal: "This version can no longer be read, so it cannot be copied.",
};

/** The refusals of a duplicate in plain sentences; the plan's (`quota`) as everywhere else. */
export function duplicateReasonText(error: unknown): string {
  const plan = planRefusalText(error);
  if (plan !== null) return plan;
  if (
    error instanceof ApiError &&
    error.reason !== null &&
    Object.hasOwn(REASONS, error.reason)
  ) {
    return REASONS[error.reason]!;
  }
  return errorMessage(error);
}
