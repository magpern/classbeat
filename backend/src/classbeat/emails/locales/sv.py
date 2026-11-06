"""The Swedish catalogue (ML6, #671): informal "du", the same ids and placeholders as ``en.py``.

Written only in translation PRs (ADR 0023 section 4). A string missing here is English; the ones
allowed to be missing are on ``untranslated.py``.
"""

from datetime import UTC, datetime

from classbeat.emails.locales.en import S

CATALOGUE: dict[S, str] = {
    S.SUBJECT_SIGNUP_CONFIRM: "Bekräfta ditt classbeat-konto",
    S.SUBJECT_ACCOUNT_EXISTS: "Du har redan ett classbeat-konto",
    S.SUBJECT_PASSWORD_RESET: "Återställ ditt classbeat-lösenord",
    S.SUBJECT_EMAIL_CHANGE_CONFIRM: "Bekräfta din nya e-postadress för classbeat",
    S.SUBJECT_EMAIL_CHANGE_TAKEN: "Ett classbeat-konto använder redan den här adressen",
    S.SUBJECT_EMAIL_CHANGED: "E-postadressen för ditt classbeat-konto har ändrats",
    S.SUBJECT_TOTP_ENROLLED: "Tvåfaktorsautentisering har aktiverats på ditt classbeat-konto",
    S.SUBJECT_MFA_FAILURES: "Misslyckade tvåfaktorsförsök på ditt classbeat-konto",
    S.SUBJECT_IDENTITY_LINKED: "En inloggningsmetod har lagts till på ditt classbeat-konto",
    S.SUBJECT_CONNECTIONS_WIPED: "Dina classbeat-anslutningar har tagits bort",
    S.SUBJECT_WELCOME: "Välkommen till classbeat",
    S.SUBJECT_PASSWORD_CHANGED: "Lösenordet för ditt classbeat-konto har ändrats",
    S.SUBJECT_ACCOUNT_DELETED: "Ditt classbeat-konto har raderats",
    S.SUBJECT_IDENTITY_UNLINKED: "En inloggningsmetod har tagits bort från ditt classbeat-konto",
    S.SUBJECT_TOTP_REMOVED: "Tvåfaktorsautentisering har stängts av på ditt classbeat-konto",
    S.FOOTER_SITE: "classbeat",
    S.FOOTER_SUPPORT: "Frågor? Skriv till",
    S.FOOTER_UNSUBSCRIBE: "Avsluta prenumerationen på de här mejlen",
    S.CONTACT_LEAD: "Kontakta oss på",
    S.REASON_REQUEST: (
        "Du får det här för att någon har använt den här e-postadressen på classbeat."
    ),
    S.REASON_SECURITY: (
        "Du får den här säkerhetsaviseringen för att det här är e-postadressen till ett "
        "classbeat-konto. Sådana aviseringar skickas för att skydda dig."
    ),
    S.SIGNUP_HEADING: "Bekräfta ditt konto",
    S.SIGNUP_PRE: "Slutför skapandet av ditt classbeat-konto.",
    S.SIGNUP_BODY: (
        "Någon har bett om att skapa ett classbeat-konto med den här adressen. För att slutföra "
        "öppnar du länken nedan (giltig i 24 timmar) och skriver det lösenord du valde."
    ),
    S.SIGNUP_BUTTON: "Bekräfta konto",
    S.SIGNUP_IGNORE: "Om det inte var du kan du bortse från det här mejlet. Inget konto skapas.",
    S.PROVIDER_SIGNUP_PRE: "Slutför skapandet av ditt classbeat-konto.",
    S.PROVIDER_SIGNUP_BODY: (
        "Någon loggade in med {provider} och bad om att skapa ett classbeat-konto med den här "
        "adressen. För att slutföra öppnar du länken nedan i samma webbläsare (giltig i 24 "
        "timmar)."
    ),
    S.EXISTS_HEADING: "Du har redan ett konto",
    S.EXISTS_PRE: "Den här adressen har redan ett classbeat-konto.",
    S.EXISTS_BODY: (
        "Någon har bett om att skapa ett classbeat-konto med den här adressen, som redan har ett."
    ),
    S.EXISTS_SIGNIN: "Logga in",
    S.EXISTS_FORGOT: "Har du glömt ditt lösenord?",
    S.EXISTS_IGNORE: "Om det inte var du kan du bortse från det här mejlet.",
    S.RESET_HEADING: "Välj ett nytt lösenord",
    S.RESET_PRE: "En länk för att välja ett nytt classbeat-lösenord.",
    S.RESET_BODY: (
        "Om du vill välja ett nytt lösenord öppnar du länken nedan (giltig i 30 minuter). När du "
        "återställer lösenordet loggas du ut överallt."
    ),
    S.RESET_BUTTON: "Välj ett nytt lösenord",
    S.RESET_IGNORE: "Om det inte var du kan du bortse från det här mejlet.",
    S.CHANGE_HEADING: "Bekräfta din nya adress",
    S.CHANGE_PRE: "Bekräfta den nya e-postadressen för ditt classbeat-konto.",
    S.CHANGE_BODY: (
        "Om du vill använda den här adressen för ditt classbeat-konto loggar du in och öppnar "
        "länken nedan (giltig i 1 timme)."
    ),
    S.CHANGE_BUTTON: "Bekräfta ny adress",
    S.CHANGE_IGNORE: "Om det inte var du kan du bortse från det här mejlet.",
    S.TAKEN_HEADING: "Den här adressen används redan",
    S.TAKEN_PRE: "Ett classbeat-konto använder redan den här adressen.",
    S.TAKEN_BODY: (
        "Någon har bett om att flytta ett classbeat-konto till den här adressen, som redan "
        "tillhör ett konto."
    ),
    S.TAKEN_SIGNIN: "Logga in",
    S.TAKEN_IGNORE: "Om det inte var du kan du bortse från det här mejlet.",
    S.CHANGED_HEADING: "Din e-postadress har ändrats",
    S.CHANGED_PRE: "E-postadressen för ditt classbeat-konto har ändrats.",
    S.CHANGED_BODY: (
        "E-postadressen för ditt classbeat-konto har ändrats. Det här är den gamla adressen."
    ),
    S.CHANGED_WHEN: "När det hände",
    S.CHANGED_WARN: "Om det inte var du, säg till oss direkt.",
    S.ACCOUNT_BUTTON: "Granska ditt konto",
    S.TOTP_HEADING: "Tvåfaktorsautentisering har aktiverats",
    S.TOTP_PRE: "En autentiseringsapp har kopplats till ditt classbeat-konto.",
    S.TOTP_BODY: "En autentiseringsapp har kopplats (eller bytts ut) på ditt classbeat-konto.",
    S.TOTP_WHEN: "När det hände",
    S.TOTP_WARN: "Om det inte var du, återställ ditt lösenord.",
    S.MFA_HEADING: "Misslyckade tvåfaktorsförsök",
    S.MFA_PRE: "Fem misslyckade tvåfaktorsförsök på ditt classbeat-konto.",
    S.MFA_BODY: (
        "Det gjordes fem misslyckade tvåfaktorsförsök på ditt classbeat-konto inom en timme."
    ),
    S.MFA_WHEN: "När vi upptäckte det",
    S.MFA_WARN: "Om det inte var du, byt lösenord.",
    S.LINKED_HEADING: "En inloggningsmetod har lagts till",
    S.LINKED_PRE: "Du kan nu logga in på ditt classbeat-konto med {provider}.",
    S.LINKED_BODY: "{provider} har kopplats till ditt classbeat-konto som ett sätt att logga in.",
    S.LINKED_WHEN: "När det hände",
    S.LINKED_WARN: (
        "Om det inte var du, logga in, ta bort den från din kontosida och byt lösenord."
    ),
    S.WELCOME_HEADING: "Välkommen till classbeat",
    S.WELCOME_PRE: "Ditt classbeat-konto är klart.",
    S.WELCOME_BODY: (
        "Beskriv ett pass så väljer classbeat låtarna, utformar passet och sparar det på ditt "
        "Intelligent Cycling-konto."
    ),
    S.WELCOME_PROVIDER: "Du kan logga in med {provider}.",
    S.WELCOME_BUTTON: "Öppna classbeat",
    S.WELCOME_REASON: (
        "Du får det här för att ett classbeat-konto just har skapats med den här adressen."
    ),
    S.PWCHANGED_HEADING: "Ditt lösenord har ändrats",
    S.PWCHANGED_PRE: "Lösenordet för ditt classbeat-konto har ändrats.",
    S.PWCHANGED_BODY: (
        "Lösenordet för ditt classbeat-konto har ändrats, och dina andra sessioner har loggats ut."
    ),
    S.PWCHANGED_WHEN: "När det hände",
    S.PWCHANGED_WARN: "Om det inte var du, återställ ditt lösenord direkt.",
    S.DELETED_HEADING: "Ditt konto har raderats",
    S.DELETED_PRE: "Ditt classbeat-konto och dess data har raderats.",
    S.DELETED_BODY: (
        "Ditt classbeat-konto och de data som sparats med det har raderats. Ingenting mer sparas "
        "under den här adressen."
    ),
    S.DELETED_WHEN: "När det hände",
    S.DELETED_WARN: "Om det inte var du, säg till oss direkt.",
    S.DELETED_REASON: (
        "Du får det här för att ditt classbeat-konto har raderats. Det här är det enda mejlet om "
        "det."
    ),
    S.UNLINKED_HEADING: "En inloggningsmetod har tagits bort",
    S.UNLINKED_PRE: "Du kan inte längre logga in på ditt classbeat-konto med {provider}.",
    S.UNLINKED_BODY: (
        "{provider} har tagits bort från ditt classbeat-konto som ett sätt att logga in."
    ),
    S.UNLINKED_WHEN: "När det hände",
    S.UNLINKED_WARN: "Om det inte var du, byt lösenord och säg till oss direkt.",
    S.TOTPOFF_HEADING: "Tvåfaktorsautentisering har stängts av",
    S.TOTPOFF_PRE: "Tvåfaktorsautentisering har stängts av på ditt classbeat-konto.",
    S.TOTPOFF_BODY: (
        "Tvåfaktorsautentisering har stängts av på ditt classbeat-konto, och dina andra "
        "sessioner har loggats ut."
    ),
    S.TOTPOFF_WHEN: "När det hände",
    S.TOTPOFF_WARN: "Om det inte var du, byt lösenord och säg till oss direkt.",
    S.WIPED_HEADING: "Dina anslutningar har tagits bort",
    S.WIPED_PRE: (
        "Som en säkerhetsåtgärd har classbeat tagit bort de sparade inloggningarna för ditt konto."
    ),
    S.WIPED_BODY: (
        "Som en säkerhetsåtgärd efter en säkerhetsincident har classbeat tagit bort de sparade "
        "inloggningarna för ditt konto."
    ),
    S.WIPED_REMOVED: "Borttagna anslutningar: {names}. Anslut dem igen från din kontosida.",
    S.WIPED_TODO: "Det här behöver du göra:",
    S.WIPED_STEP_MOWL: (
        "Intelligent Cycling (MOWL): avsluta dina andra sessioner eller byt lösenord där. "
        "MOWL-token kan inte återkallas och kan vara giltiga tills de går ut."
    ),
    S.WIPED_STEP_SPOTIFY: (
        "Spotify: ta bort classbeats åtkomst (din egen utvecklarapp) i inställningarna för ditt "
        "Spotify-konto."
    ),
    S.WIPED_STEP_SWEDEBEAT: "Swedebeat: byt ditt Swedebeat-lösenord.",
    S.WIPED_RESET: (
        "Tvåfaktorsautentiseringen har tagits bort och du har loggats ut överallt. Välj ett nytt "
        "lösenord med länken nedan (giltig i 30 minuter) och ställ sedan in "
        "tvåfaktorsautentisering igen."
    ),
    S.WIPED_RESET_BUTTON: "Välj ett nytt lösenord",
    S.WIPED_QUESTIONS: "Om du har frågor kan du kontakta oss på",
    S.PROVIDER_MOWL: "Intelligent Cycling (MOWL)",
    S.PROVIDER_SPOTIFY: "Spotify",
    S.PROVIDER_SWEDEBEAT: "Swedebeat",
    S.SUBJECT_CLASS_READY: "Ditt classbeat-pass är klart",
    S.READY_HEADING: "Ditt pass är klart",
    S.READY_PRE: "classbeat har analyserat din musik och ditt pass är klart.",
    S.READY_BODY: (
        "classbeat har analyserat musiken i ditt pass. Öppna passet för att se resultatet."
    ),
    S.READY_BUTTON: "Öppna ditt pass",
    S.READY_REASON: (
        "Du får det här för att classbeat har gjort klart ett pass som du bad om, efter att ha "
        "analyserat musiken."
    ),
}

MONTHS = (
    "januari",
    "februari",
    "mars",
    "april",
    "maj",
    "juni",
    "juli",
    "augusti",
    "september",
    "oktober",
    "november",
    "december",
)


def format_when(when: datetime) -> str:
    """A time for a notice, in UTC and saying so; month names from this catalogue, never the
    process-wide C locale (ADR 0023 section 5)."""
    utc = when.astimezone(UTC)
    return f"{utc.day} {MONTHS[utc.month - 1]} {utc.year} kl. {utc:%H:%M} UTC"
