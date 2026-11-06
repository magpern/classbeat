import { useState } from "react";
import { ApiError } from "../api/client";
import { confirmUnsubscribe } from "../api/mailPrefs";
import { AuthCard as Card } from "../components/auth/AuthCard";
import { errorMessage } from "../api/messages";
import { Notice, useAction } from "../components/forms";
import { stripFragment } from "../router/Router";

interface Tokens {
  category: string | null;
  all: string | null;
}

function readTokens(): Tokens {
  const params = new URLSearchParams(window.location.hash.replace(/^#/, ""));
  return { category: params.get("token"), all: params.get("all") };
}

/**
 * `/unsubscribe#token=...&all=...` from an optional mail. Opening the link changes nothing: the
 * person picks a button, and only then is the matching signed token sent. The tokens live in the
 * fragment (never in a server log) and are removed from the address bar on mount.
 */
export function UnsubscribePage() {
  const [tokens] = useState(() => {
    const read = readTokens();
    stripFragment();
    return read;
  });
  const [done, setDone] = useState<"category" | "all" | null>(null);
  const action = useAction();

  if (tokens.category === null && tokens.all === null) {
    return (
      <Card title="Unsubscribe">
        <Notice kind="error">This page needs the link from the mail.</Notice>
      </Card>
    );
  }
  if (done !== null) {
    return (
      <Card title="You are unsubscribed">
        <Notice kind="success">
          {done === "all"
            ? "classbeat will no longer send you optional email."
            : "classbeat will no longer send you this kind of email."}
        </Notice>
        <p>
          Sign-in, password reset and confirmation links are always sent. You
          can change which optional mail you get, including security notices,
          under Email on your account page.
        </p>
      </Card>
    );
  }

  const choose = (kind: "category" | "all", token: string) => {
    void action.run(
      async () => {
        await confirmUnsubscribe(token);
        setDone(kind);
      },
      (error) =>
        error instanceof ApiError && error.reason === "link_invalid"
          ? "This link is no longer valid. Use the link in a newer mail, or the Email section of your account."
          : errorMessage(error),
    );
  };
  return (
    <Card title="Unsubscribe">
      <p>Choose what to stop. Nothing changes until you press a button.</p>
      {action.error ? <Notice kind="error">{action.error}</Notice> : null}
      <p className="flex flex-wrap gap-2">
        {tokens.category !== null ? (
          <button
            className="button"
            type="button"
            disabled={action.busy}
            onClick={() => {
              if (tokens.category !== null) choose("category", tokens.category);
            }}
          >
            Stop this kind of email
          </button>
        ) : null}
        {tokens.all !== null ? (
          <button
            className="button"
            type="button"
            disabled={action.busy}
            onClick={() => {
              if (tokens.all !== null) choose("all", tokens.all);
            }}
          >
            Stop all optional email
          </button>
        ) : null}
      </p>
    </Card>
  );
}
