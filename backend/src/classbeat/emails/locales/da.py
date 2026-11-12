"""The Danish catalogue (ML6, #671): informal "du", the same ids and placeholders as ``en.py``.

Written only in translation PRs (ADR 0023 section 4). A string missing here is English; the ones
allowed to be missing are on ``untranslated.py``.
"""

from datetime import UTC, datetime

from classbeat.emails.locales.en import S

CATALOGUE: dict[S, str] = {
    S.SUBJECT_SIGNUP_CONFIRM: "Bekræft din classbeat-konto",
    S.SUBJECT_ACCOUNT_EXISTS: "Du har allerede en classbeat-konto",
    S.SUBJECT_PASSWORD_RESET: "Nulstil din classbeat-adgangskode",
    S.SUBJECT_EMAIL_CHANGE_CONFIRM: "Bekræft din nye e-mailadresse til classbeat",
    S.SUBJECT_EMAIL_CHANGE_TAKEN: "En classbeat-konto bruger allerede denne adresse",
    S.SUBJECT_EMAIL_CHANGED: "E-mailadressen til din classbeat-konto er blevet ændret",
    S.SUBJECT_TOTP_ENROLLED: "Tofaktorgodkendelse er slået til på din classbeat-konto",
    S.SUBJECT_MFA_FAILURES: "Mislykkede tofaktorforsøg på din classbeat-konto",
    S.SUBJECT_IDENTITY_LINKED: "En loginmetode er blevet tilføjet til din classbeat-konto",
    S.SUBJECT_CONNECTIONS_WIPED: "Dine classbeat-forbindelser er blevet fjernet",
    S.SUBJECT_WELCOME: "Velkommen til classbeat",
    S.SUBJECT_PASSWORD_CHANGED: "Adgangskoden til din classbeat-konto er blevet ændret",
    S.SUBJECT_ACCOUNT_DELETED: "Din classbeat-konto er blevet slettet",
    S.SUBJECT_IDENTITY_UNLINKED: "En loginmetode er blevet fjernet fra din classbeat-konto",
    S.SUBJECT_TOTP_REMOVED: "Tofaktorgodkendelse er slået fra på din classbeat-konto",
    S.FOOTER_SITE: "classbeat",
    S.FOOTER_SUPPORT: "Spørgsmål? Skriv til",
    S.FOOTER_UNSUBSCRIBE: "Afmeld disse mails",
    S.CONTACT_LEAD: "Kontakt os på",
    S.REASON_REQUEST: "Du får denne mail, fordi nogen har brugt denne e-mailadresse på classbeat.",
    S.REASON_SECURITY: (
        "Du får denne sikkerhedsbesked, fordi dette er e-mailadressen til en classbeat-konto. "
        "Sådanne beskeder sendes for at beskytte dig."
    ),
    S.SIGNUP_HEADING: "Bekræft din konto",
    S.SIGNUP_PRE: "Gør oprettelsen af din classbeat-konto færdig.",
    S.SIGNUP_BODY: (
        "Nogen har bedt om at oprette en classbeat-konto med denne adresse. For at gøre det "
        "færdigt skal du åbne linket nedenfor (gyldigt i 24 timer) og skrive den adgangskode, du "
        "valgte."
    ),
    S.SIGNUP_BUTTON: "Bekræft konto",
    S.SIGNUP_IGNORE: (
        "Hvis det ikke var dig, kan du se bort fra denne mail. Der oprettes ingen konto."
    ),
    S.PROVIDER_SIGNUP_PRE: "Gør oprettelsen af din classbeat-konto færdig.",
    S.PROVIDER_SIGNUP_BODY: (
        "Nogen loggede ind med {provider} og bad om at oprette en classbeat-konto med denne "
        "adresse. For at gøre det færdigt skal du åbne linket nedenfor i den samme browser "
        "(gyldigt i 24 timer)."
    ),
    S.EXISTS_HEADING: "Du har allerede en konto",
    S.EXISTS_PRE: "Denne adresse har allerede en classbeat-konto.",
    S.EXISTS_BODY: (
        "Nogen har bedt om at oprette en classbeat-konto med denne adresse, som allerede har en."
    ),
    S.EXISTS_SIGNIN: "Log ind",
    S.EXISTS_FORGOT: "Har du glemt din adgangskode?",
    S.EXISTS_IGNORE: "Hvis det ikke var dig, kan du se bort fra denne mail.",
    S.RESET_HEADING: "Vælg en ny adgangskode",
    S.RESET_PRE: "Et link til at vælge en ny classbeat-adgangskode.",
    S.RESET_BODY: (
        "Hvis du vil vælge en ny adgangskode, skal du åbne linket nedenfor (gyldigt i 30 "
        "minutter). Når du nulstiller adgangskoden, bliver du logget ud overalt."
    ),
    S.RESET_BUTTON: "Vælg en ny adgangskode",
    S.RESET_IGNORE: "Hvis det ikke var dig, kan du se bort fra denne mail.",
    S.CHANGE_HEADING: "Bekræft din nye adresse",
    S.CHANGE_PRE: "Bekræft den nye e-mailadresse til din classbeat-konto.",
    S.CHANGE_BODY: (
        "Hvis du vil bruge denne adresse til din classbeat-konto, skal du logge ind og åbne "
        "linket nedenfor (gyldigt i 1 time)."
    ),
    S.CHANGE_BUTTON: "Bekræft ny adresse",
    S.CHANGE_IGNORE: "Hvis det ikke var dig, kan du se bort fra denne mail.",
    S.TAKEN_HEADING: "Denne adresse er allerede i brug",
    S.TAKEN_PRE: "En classbeat-konto bruger allerede denne adresse.",
    S.TAKEN_BODY: (
        "Nogen har bedt om at flytte en classbeat-konto til denne adresse, som allerede "
        "tilhører en konto."
    ),
    S.TAKEN_SIGNIN: "Log ind",
    S.TAKEN_IGNORE: "Hvis det ikke var dig, kan du se bort fra denne mail.",
    S.CHANGED_HEADING: "Din e-mailadresse er blevet ændret",
    S.CHANGED_PRE: "E-mailadressen til din classbeat-konto er blevet ændret.",
    S.CHANGED_BODY: (
        "E-mailadressen til din classbeat-konto er blevet ændret. Dette er den gamle adresse."
    ),
    S.CHANGED_WHEN: "Hvornår det skete",
    S.CHANGED_WARN: "Hvis det ikke var dig, skal du give os besked med det samme.",
    S.ACCOUNT_BUTTON: "Gennemgå din konto",
    S.TOTP_HEADING: "Tofaktorgodkendelse er slået til",
    S.TOTP_PRE: "En godkendelsesapp er blevet sat op på din classbeat-konto.",
    S.TOTP_BODY: (
        "En godkendelsesapp er blevet tilknyttet (eller udskiftet) på din classbeat-konto."
    ),
    S.TOTP_WHEN: "Hvornår det skete",
    S.TOTP_WARN: "Hvis det ikke var dig, skal du nulstille din adgangskode.",
    S.MFA_HEADING: "Mislykkede tofaktorforsøg",
    S.MFA_PRE: "Fem mislykkede tofaktorforsøg på din classbeat-konto.",
    S.MFA_BODY: ("Der var fem mislykkede tofaktorforsøg på din classbeat-konto inden for en time."),
    S.MFA_WHEN: "Hvornår vi opdagede det",
    S.MFA_WARN: "Hvis det ikke var dig, skal du skifte din adgangskode.",
    S.LINKED_HEADING: "En loginmetode er blevet tilføjet",
    S.LINKED_PRE: "Du kan nu logge ind på din classbeat-konto med {provider}.",
    S.LINKED_BODY: (
        "{provider} er blevet knyttet til din classbeat-konto som en måde at logge ind på."
    ),
    S.LINKED_WHEN: "Hvornår det skete",
    S.LINKED_WARN: (
        "Hvis det ikke var dig, skal du logge ind, fjerne den på din kontoside og skifte din "
        "adgangskode."
    ),
    S.WELCOME_HEADING: "Velkommen til classbeat",
    S.WELCOME_PRE: "Din classbeat-konto er klar.",
    S.WELCOME_BODY: (
        "Beskriv et hold, så vælger classbeat sangene, designer holdet og gemmer det på din "
        "Intelligent Cycling-konto."
    ),
    S.WELCOME_PROVIDER: "Du kan logge ind med {provider}.",
    S.WELCOME_BUTTON: "Åbn classbeat",
    S.WELCOME_REASON: (
        "Du får denne mail, fordi der netop er oprettet en classbeat-konto med denne adresse."
    ),
    S.PWCHANGED_HEADING: "Din adgangskode er blevet ændret",
    S.PWCHANGED_PRE: "Adgangskoden til din classbeat-konto er blevet ændret.",
    S.PWCHANGED_BODY: (
        "Adgangskoden til din classbeat-konto er blevet ændret, og dine andre sessioner er "
        "blevet logget ud."
    ),
    S.PWCHANGED_WHEN: "Hvornår det skete",
    S.PWCHANGED_WARN: "Hvis det ikke var dig, skal du nulstille din adgangskode med det samme.",
    S.DELETED_HEADING: "Din konto er blevet slettet",
    S.DELETED_PRE: "Din classbeat-konto og dens data er blevet slettet.",
    S.DELETED_BODY: (
        "Din classbeat-konto og de data, der var gemt sammen med den, er blevet slettet. Der "
        "gemmes ikke mere under denne adresse."
    ),
    S.DELETED_WHEN: "Hvornår det skete",
    S.DELETED_WARN: "Hvis det ikke var dig, skal du give os besked med det samme.",
    S.DELETED_REASON: (
        "Du får denne mail, fordi din classbeat-konto er blevet slettet. Det er den eneste mail "
        "om det."
    ),
    S.UNLINKED_HEADING: "En loginmetode er blevet fjernet",
    S.UNLINKED_PRE: "Du kan ikke længere logge ind på din classbeat-konto med {provider}.",
    S.UNLINKED_BODY: (
        "{provider} er blevet fjernet fra din classbeat-konto som en måde at logge ind på."
    ),
    S.UNLINKED_WHEN: "Hvornår det skete",
    S.UNLINKED_WARN: (
        "Hvis det ikke var dig, skal du skifte din adgangskode og give os besked med det samme."
    ),
    S.TOTPOFF_HEADING: "Tofaktorgodkendelse er slået fra",
    S.TOTPOFF_PRE: "Tofaktorgodkendelse er slået fra på din classbeat-konto.",
    S.TOTPOFF_BODY: (
        "Tofaktorgodkendelse er slået fra på din classbeat-konto, og dine andre sessioner er "
        "blevet logget ud."
    ),
    S.TOTPOFF_WHEN: "Hvornår det skete",
    S.TOTPOFF_WARN: (
        "Hvis det ikke var dig, skal du skifte din adgangskode og give os besked med det samme."
    ),
    S.WIPED_HEADING: "Dine forbindelser er blevet fjernet",
    S.WIPED_PRE: ("Som en forholdsregel har classbeat fjernet de gemte logins til din konto."),
    S.WIPED_BODY: (
        "Som en forholdsregel efter en sikkerhedshændelse har classbeat fjernet de "
        "gemte logins til din konto."
    ),
    S.WIPED_REMOVED: (
        "Fjernede forbindelser: {names}. Opret forbindelse til dem igen fra din kontoside."
    ),
    S.WIPED_TODO: "Det skal du gøre:",
    S.WIPED_STEP_MOWL: (
        "Intelligent Cycling (MOWL): afslut dine andre sessioner, eller skift adgangskode dér. "
        "MOWL-tokens kan ikke tilbagekaldes og kan forblive gyldige, indtil de udløber."
    ),
    S.WIPED_STEP_SPOTIFY: (
        "Spotify: fjern classbeats adgang (din egen udviklerapp) i indstillingerne for din "
        "Spotify-konto."
    ),
    S.WIPED_STEP_SWEDEBEAT: "Swedebeat: skift din Swedebeat-adgangskode.",
    S.WIPED_RESET: (
        "Tofaktorgodkendelsen er blevet fjernet, og du er blevet logget ud overalt. Vælg en ny "
        "adgangskode med linket nedenfor (gyldigt i 30 minutter), og sæt derefter "
        "tofaktorgodkendelse op igen."
    ),
    S.WIPED_RESET_BUTTON: "Vælg en ny adgangskode",
    S.WIPED_QUESTIONS: "Hvis du har spørgsmål, kan du kontakte os på",
    S.PROVIDER_MOWL: "Intelligent Cycling (MOWL)",
    S.PROVIDER_SPOTIFY: "Spotify",
    S.PROVIDER_SWEDEBEAT: "Swedebeat",
    S.SUBJECT_CLASS_READY: "Dit classbeat-hold er klar",
    S.READY_HEADING: "Dit hold er klar",
    S.READY_PRE: "classbeat har analyseret din musik, og dit hold er klar.",
    S.READY_BODY: (
        "classbeat har analyseret musikken til dit hold. Åbn holdet for at se resultatet."
    ),
    S.READY_BUTTON: "Åbn dit hold",
    S.READY_REASON: (
        "Du får denne mail, fordi classbeat har gjort et hold færdigt, som du bad om, efter at "
        "have analyseret musikken."
    ),
    S.SUBJECT_PASSKEY_ADDED: "Der er tilføjet en passkey til din classbeat-konto",
    S.SUBJECT_PASSKEY_REMOVED: "Der er fjernet en passkey fra din classbeat-konto",
    S.PASSKEY_ADDED_HEADING: "Der er tilføjet en passkey",
    S.PASSKEY_ADDED_PRE: "Du kan nu logge ind på din classbeat-konto med en ny passkey.",
    S.PASSKEY_ADDED_BODY: (
        'En passkey med navnet "{label}" er tilføjet til din classbeat-konto som en måde at '
        "logge ind på."
    ),
    S.PASSKEY_ADDED_WARN: (
        "Hvis det ikke var dig, skal du fjerne passkeyen fra din kontoside, skifte din "
        "adgangskode og give os besked med det samme."
    ),
    S.PASSKEY_REMOVED_HEADING: "Der er fjernet en passkey",
    S.PASSKEY_REMOVED_PRE: "Du kan ikke længere logge ind på din classbeat-konto med en passkey.",
    S.PASSKEY_REMOVED_BODY: 'Passkeyen med navnet "{label}" er fjernet fra din classbeat-konto.',
    S.PASSKEY_REMOVED_WARN: (
        "Hvis det ikke var dig, skal du skifte din adgangskode og give os besked med det samme."
    ),
    S.PASSKEY_WHEN: "Hvornår det skete",
    S.SUBJECT_PASSKEY_COUNTER: "En passkey på din classbeat-konto blev afvist",
    S.PASSKEY_COUNTER_HEADING: "En passkey blev afvist",
    S.PASSKEY_COUNTER_PRE: "En passkey på din classbeat-konto kan være blevet kopieret.",
    S.PASSKEY_COUNTER_BODY: (
        'Passkeyen "{label}" blev afvist, da den blev brugt til at logge ind eller bekræfte, at '
        "det var dig, fordi dens signaturtæller ikke var gået op. Det kan ske, når en passkey er "
        "blevet kopieret til en anden enhed, og af og til når to forespørgsler ankommer "
        "samtidigt. Passkeyen virker stadig."
    ),
    S.PASSKEY_COUNTER_WARN: (
        "Hvis du ikke lige har brugt denne passkey, skal du fjerne den fra din "
        "kontoside, skifte din adgangskode og give os besked med det samme."
    ),
    S.PASSKEY_MFA_RESET_NOTE: (
        "Vi har fjernet tofaktorgodkendelsen, gendannelseskoderne og passkeys på din "
        "classbeat-konto, som du bad om for at få adgang igen. Vælg en ny adgangskode, og sæt "
        "dem derefter op igen på din kontoside."
    ),
    S.PWCHANGED_PASSKEYS: (
        "Dine passkeys er også blevet fjernet. Log ind med din nye adgangskode, og tilføj dem "
        "derefter igen på din kontoside."
    ),
    S.PASSKEY_MFA_RESET_WARN: "Hvis du ikke bad om dette, skal du give os besked med det samme.",
    S.SUBJECT_INVITE_CODE: "Din invitationskode til classbeat",
    S.INVITE_HEADING: "Du er inviteret til classbeat",
    S.INVITE_PRE: "Her er din invitationskode til classbeat.",
    S.INVITE_BODY: "Tak, fordi du spurgte. Brug denne invitationskode, når du opretter din konto:",
    S.INVITE_BUTTON: "Opret din konto",
    S.INVITE_NOTE: (
        "Koden virker én gang og udløber om {days} dage. Hvis du ikke har bedt om en invitation, "
        "kan du se bort fra denne mail."
    ),
}

MONTHS = (
    "januar",
    "februar",
    "marts",
    "april",
    "maj",
    "juni",
    "juli",
    "august",
    "september",
    "oktober",
    "november",
    "december",
)


def format_when(when: datetime) -> str:
    """A time for a notice, in UTC and saying so; month names from this catalogue, never the
    process-wide C locale (ADR 0023 section 5)."""
    utc = when.astimezone(UTC)
    return f"{utc.day}. {MONTHS[utc.month - 1]} {utc.year} kl. {utc:%H:%M} UTC"
