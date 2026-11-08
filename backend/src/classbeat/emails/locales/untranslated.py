"""Mail strings English has and ``sv.py`` / ``da.py`` may lack (ADR 0023 section 4).

A feature PR that adds or changes an English string lists its id here (and removes the stale
Swedish and Danish text); a catch-up translation PR translates it and empties the list. CI fails on
a missing string that is not listed and on a listed one that is translated or no longer exists
(``tests/test_emails_locales.py``). Empty at the merge of ML6 (#671).
"""

from classbeat.emails.locales.en import S

# ADR 0019 W2 (#772): the passkey added and removed notices.
UNTRANSLATED: frozenset[S] = frozenset(
    {
        S.SUBJECT_PASSKEY_ADDED,
        S.SUBJECT_PASSKEY_REMOVED,
        S.PASSKEY_ADDED_HEADING,
        S.PASSKEY_ADDED_PRE,
        S.PASSKEY_ADDED_BODY,
        S.PASSKEY_ADDED_WARN,
        S.PASSKEY_REMOVED_HEADING,
        S.PASSKEY_REMOVED_PRE,
        S.PASSKEY_REMOVED_BODY,
        S.PASSKEY_REMOVED_WARN,
        S.PASSKEY_WHEN,
        # ADR 0019 W3 (#773): a passkey sign-in refused for its counter.
        S.SUBJECT_PASSKEY_COUNTER,
        S.PASSKEY_COUNTER_HEADING,
        S.PASSKEY_COUNTER_PRE,
        S.PASSKEY_COUNTER_BODY,
        S.PASSKEY_COUNTER_WARN,
        # ADR 0019 W5 (#775): the ``admin mfa-reset`` link says what it removed.
        S.PASSKEY_MFA_RESET_NOTE,
        S.PASSKEY_MFA_RESET_WARN,
    }
)
