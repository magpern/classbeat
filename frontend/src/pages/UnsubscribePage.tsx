import { useState } from "react";
import { ApiError } from "../api/client";
import { confirmUnsubscribe } from "../api/mailPrefs";
import { AuthCard as Card } from "../components/auth/AuthCard";
import { errorMessage } from "../api/messages";
import { useT } from "../i18n/I18n";
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
  const t = useT();
  const [tokens] = useState(() => {
    const read = readTokens();
    stripFragment();
    return read;
  });
  const [done, setDone] = useState<"category" | "all" | null>(null);
  const action = useAction();

  if (tokens.category === null && tokens.all === null) {
    return (
      <Card title={t("unsub.title")}>
        <Notice kind="error">{t("unsub.needsLink")}</Notice>
      </Card>
    );
  }
  if (done !== null) {
    return (
      <Card title={t("unsub.doneTitle")}>
        <Notice kind="success">{done === "all" ? t("unsub.doneAll") : t("unsub.doneCategory")}</Notice>
        <p>{t("unsub.doneNote")}</p>
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
          ? t("unsub.linkInvalid")
          : errorMessage(error),
    );
  };
  return (
    <Card title={t("unsub.title")}>
      <p>{t("unsub.intro")}</p>
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
            {t("unsub.stopCategory")}
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
            {t("unsub.stopAll")}
          </button>
        ) : null}
      </p>
    </Card>
  );
}
