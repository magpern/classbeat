"""Mail strings English has and ``sv.py`` / ``da.py`` may lack (ADR 0023 section 4).

A feature PR that adds or changes an English string lists its id here (and removes the stale
Swedish and Danish text); a catch-up translation PR translates it and empties the list. CI fails on
a missing string that is not listed and on a listed one that is translated or no longer exists
(``tests/test_emails_locales.py``). Empty at the merge of ML6 (#671) and again once the
passkey strings of ADR 0019 W2, W3 and W5 were translated.
"""

from classbeat.emails.locales.en import S

UNTRANSLATED: frozenset[S] = frozenset()
