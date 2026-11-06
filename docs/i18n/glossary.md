# i18n glossary: Swedish and Danish

The shared vocabulary of the Swedish (`sv`) and Danish (`da`) app catalogues
(`frontend/src/i18n/sv.ts`, `da.ts`, ML7) and the mail catalogues
(`backend/src/classbeat/emails/locales/sv.py`, `da.py`, ML6). A term is translated one way
everywhere; a change here changes both places in the same PR. ADR 0023 has the design.

## Register and conventions

- Informal "du" in both languages. No "ni", no "De".
- Placeholders (`{name}`) and plural keys are exactly the English ones. Swedish and Danish have two
  plural forms, `one` and `other`.
- Quotation marks: Swedish `”…”`, Danish `»…«`. No ASCII apostrophe inside a string; write
  "classbeats", "Spotifys", "MOWLs" (no apostrophe before the genitive s, which is correct in both
  languages for these names).
- Hours: Swedish `h`, Danish `t`. Month names are lowercase. `plan.dayMonth` is `{day} {month}`
  (sv) and `{day}. {month}` (da).
- Brand and product names stay as they are: classbeat, MOWL, Intelligent Cycling, Spotify,
  Swedebeat, FTP.
- Legal and consent sentences (the beta line on the sign-in page, the Spotify pool consent, the
  export and delete confirmations) are translated by the developer and checked by agent review only.
  Generated classes and the admin pages stay English-only.

## Terms

| English | Swedish | Danish | Note |
|---|---|---|---|
| class | pass | hold | The thing an instructor teaches and classbeat designs. |
| workout (a MOWL workout) | träningspass | træningspas | Only where MOWL's own object is meant. |
| zone | zon | zone | Effort zone. |
| cooldown | nedvarvning | nedkøling | |
| warm-up | uppvärmning | opvarmning | |
| FTP test | FTP-test | FTP-test | |
| cadence | kadens | kadence | |
| interval, intervals | intervall, intervaller | interval, intervaller | |
| climb | klättring | stigning | |
| sprint | sprint (pl. sprintar) | sprint (pl. sprints) | |
| incline | lutning | hældning | |
| standing, seated | stående, sittande | stående, siddende | |
| song | låt | sang | |
| playlist | spellista | playliste | Danish "playliste" is common gender. |
| working playlist | arbetsspellista | arbejdsplayliste | |
| draft | utkast | udkast | |
| save | spara | gem | |
| design (verb) | utforma | designe | |
| designer (the model) | utformaren | designeren | |
| sign in | logga in | log ind | |
| sign-in (noun) | inloggning | login | |
| sign up | registrera dig | opret dig | |
| password | lösenord | adgangskode | |
| invite code | inbjudningskod | invitationskode | |
| two-factor | tvåfaktorsautentisering | tofaktorgodkendelse | |
| authenticator app | autentiseringsapp | godkendelsesapp | |
| recovery code | återställningskod | gendannelseskode | |
| provider | leverantör | udbyder | Sign-in provider. |
| email | e-post | mail | Mail catalogues use "e-postadress" / "mailadresse" for the address. |
| connect, disconnect | ansluta, koppla från | tilslutte, afbryde | |
| consent | samtycke | samtykke | |
| trial | provperiod | prøveperiode | |
| a precaution | försiktighetsåtgärd | forholdsregel | Same word as in the ML6 mails. |
| race (Spotify/MOWL term) | race | race | Loan word. |
| beat | beat (invariant plural) | beat (invariant plural) | Loan word. |

## Decisions and open notes

- **"1 intervals" (open).** English `class.profile.label` reads "Class profile, {length}, {count}
  intervals" and is not pluralised, so a one-interval class reads "1 intervals". Both catalogues
  keep the same shape and say "intervaller" for every count. Decide with the translation whether
  the key becomes a plural in English first (then sv and da follow with `interval` / `intervaller`);
  until then it is a known quirk, not a translation error.
- **`class.revisions.segments`** is `{count} segment` (sv) and `{count} segmenter` (da): the
  Swedish noun is invariant, the Danish one is not, and the key is not a plural in English.
- **Rate limits** read "sakta ner" (sv) and "sætte tempoet ned" (da).
- **`job.toolCalls`** uses "gång(er)" (sv) and "gang(e)" (da) because the English key does not
  pluralise.
- **`language.help`** still says mail stays English until more languages exist, as the English key
  does (ML6 changed that); fix it in English first, then here.
- **`plan.quota.feature.new`** has an English singular/plural mismatch ("Take it out" / "Take them
  out"); the translations follow the English faithfully.
- **Danish gender.** "konto" is common gender: "en konto", "kontoen".
