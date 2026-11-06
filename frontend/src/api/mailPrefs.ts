import { get, post, request } from "./client";
import { flag, object, strings } from "./decode";

// Email preferences (ADR 0022, backend/src/classbeat/auth/mail_routes.py). Only optional kinds
// can be switched; sign-in, password reset and confirmation links are always sent. The security
// notices are the optional `account_activity` kind.

export const MAIL_CATEGORIES = [
  "welcome",
  "account_activity",
  "class_ready",
] as const;
export type MailCategory = (typeof MAIL_CATEGORIES)[number];

export interface MailPreferences {
  unsubscribedAll: boolean;
  optedOut: string[];
}

function decode(value: unknown): MailPreferences {
  const item = object(value, "mail preferences");
  return {
    unsubscribedAll: flag(item, "unsubscribed_all"),
    optedOut: strings(item, "opted_out"),
  };
}

export async function getMailPreferences(
  signal?: AbortSignal,
): Promise<MailPreferences> {
  return decode(await get("/api/mail/preferences", signal));
}

export async function setMailPreferences(
  prefs: MailPreferences,
): Promise<MailPreferences> {
  const body = {
    unsubscribed_all: prefs.unsubscribedAll,
    opted_out: prefs.optedOut,
  };
  return decode(await request("PUT", "/api/mail/preferences", { body }));
}

/** The unsubscribe page's button: the signed token is the credential; it can only turn mail off. */
export async function confirmUnsubscribe(token: string): Promise<void> {
  await post("/api/mail/unsubscribe/confirm", { token });
}
