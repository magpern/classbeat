import { useState } from "react";
import {
  passkeyErrorMessage,
  signInWithPasskey,
  type SignInOutcome,
} from "../../auth/passkeyCeremony";
import { PasskeyCancelled } from "../../auth/webauthn";
import { useT } from "../../i18n/I18n";
import { Notice } from "../forms";
import { usePasskeyAutofill } from "./usePasskeyAutofill";
import { Button } from "../ui/button";

interface Props {
  /** Finishes the sign-in (refresh the session and navigate); throws on failure. */
  onSignedIn: (outcome: SignInOutcome) => Promise<void>;
}

/**
 * "Use a passkey" (ADR 0019 section 5) and the autofill that shares its ceremony. The
 * caller renders it only where the flag is on and the browser supports WebAuthn.
 */
export function PasskeySignIn({ onSignedIn }: Props) {
  const t = useT();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  usePasskeyAutofill({ paused: busy, onSignedIn, onError: setError });

  const click = async () => {
    setBusy(true);
    setError(null);
    try {
      await onSignedIn(await signInWithPasskey());
    } catch (caught) {
      if (!(caught instanceof PasskeyCancelled))
        setError(passkeyErrorMessage(caught));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      {error ? <Notice kind="error">{error}</Notice> : null}
      <Button
        variant="secondary"
        className="w-full"
        type="button"
        disabled={busy}
        onClick={() => {
          void click();
        }}
      >
        {t("passkey.signIn")}
      </Button>
    </>
  );
}
