import type { Catalogue } from './en'

const sv: Catalogue = {
  'language.label': 'Språk',
  'language.follow': 'Följ den här webbläsaren',
  'language.help':
    'Språket på den här webbplatsen. ”Följ den här webbläsaren” använder webbläsarens språk på varje enhet; mejl skickas på engelska tills fler språk finns.',
  'format.soonUnknown': 'om några minuter',
  'usage.underOneCent': 'under {amount}',

  // Words the screens of this area share.
  'common.loading': 'Laddar',
  'common.cancel': 'Avbryt',
  'common.continue': 'Fortsätt',
  'common.email': 'E-post',
  'common.password': 'Lösenord',

  // Landing page.
  'landing.badge': 'Privat beta',
  'landing.title':
    'Beskriv ditt inomhuscykelpass, så väljer classbeat låtarna och tidsätter ansträngningarna efter musiken.',
  'landing.signUp': 'Registrera dig med en inbjudningskod',
  'landing.haveAccount': 'Jag har redan ett konto',
  'landing.example.title': 'Ett exempelpass',
  'landing.example.note': 'En påhittad tur på 30 minuter. Låtarna och artisterna nedan är platshållare.',
  'landing.example.songs': 'Låtarna i exempelpasset',
  'landing.example.songMeta': '{artist} · {part}',
  'landing.example.songStats': '{bpm} BPM · {length}',
  'landing.example.part.warmUp': 'Uppvärmning',
  'landing.example.part.firstClimb': 'Första klättringen',
  'landing.example.part.sprints': 'Sprintar',
  'landing.example.part.secondClimb': 'Andra klättringen',
  'landing.example.part.coolDown': 'Nedvarvning',
  'landing.how.title': 'Så fungerar det',
  'landing.how.describe.title': 'Beskriv',
  'landing.how.describe.text':
    'Berätta för classbeat hur långt passet ska vara, hur hårt det ska vara och vilken spellista du vill använda.',
  'landing.how.approve.title': 'Godkänn låtar',
  'landing.how.approve.text': 'Titta igenom låtarna som classbeat har valt och byt ut de som inte passar.',
  'landing.how.save.title': 'Spara i MOWL',
  'landing.how.save.text': 'Ditt färdiga pass skickas direkt till ditt MOWL-konto, redo att cykla.',

  // Sign in, sign up and the pages behind the mail links.
  'auth.signIn': 'Logga in',
  'auth.createAccount': 'Skapa ett konto',
  'auth.createAccountSubmit': 'Skapa konto',
  'auth.newHere': 'Ny här? Skapa ett konto',
  'auth.haveAccountSignIn': 'Har du redan ett konto? Logga in',
  'auth.inviteLead': 'classbeat är än så länge bara för inbjudna. Skriv in inbjudningskoden du fick.',
  'auth.inviteFirst': 'Skriv först in din inbjudningskod.',
  'auth.inviteCode': 'Inbjudningskod',
  'auth.continueWith': 'Fortsätt med {provider}',
  'auth.or': 'ELLER',
  'auth.passwordHint': '12 till 128 tecken. Du skriver det igen när du bekräftar.',
  'auth.legal':
    'Genom att fortsätta godkänner du att delta i betan, som bara är för inbjudna. classbeat är inte gjort av eller kopplat till MOWL, Spotify eller Swedebeat.',
  'auth.forgot': 'Har du glömt ditt lösenord?',
  'auth.checkInbox': 'Kolla din inkorg',
  'auth.backToSignIn': 'Tillbaka till inloggningen',
  'signup.sent':
    'Om den här adressen kan användas har vi skickat en länk till den. Öppna länken och skriv ditt lösenord igen för att slutföra. Länken fungerar i 24 timmar.',
  'confirm.title': 'Bekräfta ditt konto',
  'confirm.noLink':
    'Den här sidan behöver länken från bekräftelsemejlet. Öppna länken igen, eller registrera dig på nytt för att få en ny.',
  'confirm.doneTitle': 'Kontot är skapat',
  'confirm.done': 'Ditt konto är klart. Logga in för att fortsätta.',
  'confirm.intro':
    'Skriv det lösenord du valde när du registrerade dig. Om du registrerade dig med en leverantör som Microsoft lämnar du det tomt.',
  'confirm.submit': 'Bekräfta konto',
  'emailChange.title': 'Bekräfta din nya e-postadress',
  'emailChange.noLink': 'Den här sidan behöver länken från bekräftelsemejlet.',
  'emailChange.changedTitle': 'E-postadressen är ändrad',
  'emailChange.changed': 'Ditt konto använder nu {email}. Andra enheter har loggats ut.',
  'emailChange.back': 'Tillbaka till ditt konto',
  'emailChange.signInFirst': 'Logga in på kontot som bad om ändringen och öppna sedan länken i mejlet igen.',
  'emailChange.confirmIntro':
    'Du är inloggad som {email}. Bekräfta att det här kontot ska använda den nya adressen.',
  'emailChange.confirm': 'Bekräfta ny e-postadress',
  'providerEmail.loadingTitle': 'Nästan klart',
  'providerEmail.sent':
    'Om vi kan använda {email} är en bekräftelselänk på väg. Öppna den i samma webbläsare för att slutföra. Länken fungerar i 24 timmar.',
  'providerEmail.mistyped':
    'Skrev du fel adress? Det går inte att skicka något mer från den här sidan, eftersom ett andra försök inte skulle skicka något. Starta inloggningen hos leverantören igen för att få en ny chans.',
  'providerEmail.again': 'Starta inloggningen hos leverantören igen',
  'providerEmail.nothingTitle': 'Inget att slutföra här',
  'providerEmail.nothing':
    'Ingen inloggning hos en leverantör väntar på en adress i den här webbläsaren. Den kan ha gått ut (efter 15 minuter) eller redan vara slutförd.',
  'providerEmail.yourProvider': 'din leverantör',
  'providerEmail.title': 'Bekräfta din e-postadress',
  'providerEmail.intro':
    '{provider} bekräftade ingen e-postadress åt dig. Skriv in adressen du vill använda för classbeat; vi mejlar dig en länk för att bekräfta den.',
  'providerEmail.submit': 'Skicka bekräftelselänk',
  'reset.sent':
    'Om ett konto använder den adressen är en länk för att välja ett nytt lösenord på väg. Den fungerar i 30 minuter.',
  'reset.title': 'Återställ ditt lösenord',
  'reset.intro': 'Skriv in din e-postadress så mejlar vi dig en länk.',
  'reset.send': 'Skicka återställningslänk',
  'reset.changedTitle': 'Lösenordet är ändrat',
  'reset.changed': 'Ditt lösenord är ändrat och du är utloggad överallt. Logga in med det nya.',
  'reset.chooseTitle': 'Välj ett nytt lösenord',
  'reset.checking': 'Kontrollerar din länk',
  'reset.newLink': 'Få en ny länk',
  'reset.newPassword': 'Nytt lösenord',
  'reset.passwordHint': '12 till 128 tecken.',
  'reset.linked': 'Kopplade inloggningsmetoder',
  'reset.linkedHint':
    'Med dem kan man också logga in på ditt konto. Ta bort de du inte känner igen eller inte längre använder.',
  'reset.remove': 'Ta bort {provider}',
  'reset.submit': 'Ändra lösenord',

  // The confirm-it-is-you prompts of the guard.
  'guard.reauthTitle': 'Bekräfta att det är du',
  'guard.reauthIntro':
    'Den här ändringen kräver en nyligen gjord inloggning. Bekräfta din identitet för att fortsätta.',
  'guard.code': 'Kod från autentiseringsappen eller återställningskod',
  'guard.confirmPassword': 'Bekräfta med lösenord',
  'guard.confirmCode': 'Bekräfta med kod',
  'guard.confirmProvider': 'Bekräfta med {provider}',
  'guard.oidc': '{providers} kan bekräfta det bara om du har loggat in där de senaste 5 minuterna.',
  'guard.oidcPassword':
    '{providers} kan bekräfta det bara om du har loggat in där de senaste 5 minuterna. Ditt lösenord fungerar alltid.',
  'guard.stepUpTitle': 'Skriv en tvåfaktorskod',
  'guard.stepUpIntro':
    'Den här ändringen kräver en kod från din autentiseringsapp, eller en av dina återställningskoder.',

  // The frame around every page.
  'shell.nav.main': 'Huvudmeny',
  'shell.nav.home': 'Start',
  'shell.nav.classes': 'Mina pass',
  'shell.nav.new': 'Nytt pass',
  'shell.nav.music': 'Musik',
  'shell.nav.account': 'Konto',
  'shell.brandHome': 'classbeat startsida',
  'shell.signUp': 'Registrera dig',
  'shell.backHome': 'Tillbaka till startsidan',
  'shell.close': 'Stäng',
  'shell.menu.signOut': 'Logga ut',
  'shell.classesThisMonth': 'Pass den här månaden',
  'shell.classesThisMonthLine': 'Pass den här månaden: {line}',
  'shell.api.checking': 'API: kontrollerar…',
  'shell.api.ok': 'API: ok',
  'shell.api.unreachable': 'API: nås inte',
  'shell.admin': 'Admin',
  'shell.notAdmin': 'Ditt konto är inte ett adminkonto.',
  'shell.notFound': 'Sidan finns inte',
  'shell.backToStart': 'Tillbaka till start',
  'shell.requireAuth.loadingTitle': 'Ett ögonblick',
  'shell.requireAuth.errorTitle': 'Det går inte att nå servern',
  'shell.requireAuth.errorTryAgain': 'Försök igen om en stund.',
  'shell.requireAuth.title': 'Logga in först',
  'shell.requireAuth.text': 'Den här sidan är för inloggade användare.',

  // Plain-language texts for the reason codes of the auth API (api/messages.ts).
  'error.reason.confirmation_mismatch': 'Det är inte e-postadressen till det här kontot. Inget har raderats.',
  'error.reason.invalid_invite': 'Den inbjudningskoden är inte giltig, eller så har den redan använts.',
  'error.reason.invite_invalid': 'Inbjudan för den här registreringen är inte längre giltig. Be om en ny.',
  'error.reason.signups_closed': 'Registreringen är stängd just nu.',
  'error.reason.password_rule': 'Välj ett lösenord på 12 till 128 tecken.',
  'error.reason.invalid_credentials': 'E-postadressen eller lösenordet stämmer inte.',
  'error.reason.wrong_password': 'Det är inte det lösenord du valde när du registrerade dig.',
  'error.reason.invalid_token':
    'Den här länken är inte giltig längre. Den kan ha gått ut eller redan ha använts.',
  'error.reason.wrong_browser':
    'Slutför på den enhet och i den webbläsare där du startade inloggningen. Länken fungerar inte här.',
  'error.reason.account_exists': 'Ett konto med den här adressen finns redan. Logga in på det i stället.',
  'error.reason.email_taken': 'Den adressen används redan av ett annat konto.',
  'error.reason.last_sign_in_method': 'Det här är ditt enda sätt att logga in, så det kan inte tas bort.',
  'error.reason.invalid_code': 'Koden stämmer inte. Kontrollera appen och försök igen.',
  'error.reason.totp_changed': 'Din tvåfaktorsinställning ändrades under tiden. Börja om.',
  'error.reason.totp_not_enabled': 'Tvåfaktorsautentisering är inte inställd.',
  'error.reason.not_authenticated': 'Du är utloggad. Logga in och försök igen.',
  'error.reason.no_pending_signup':
    'Ingen inloggning väntar på en adress. Starta inloggningen hos leverantören igen.',
  'error.reason.provider_disabled': 'Den inloggningsmetoden är inte tillgänglig.',
  'error.reason.reauth_use_password': 'Ditt konto har ett lösenord, så bekräfta med ditt lösenord.',
  'error.reason.forbidden': 'Ditt konto får inte göra det.',
  'error.reason.busy': 'Servern är upptagen. Försök igen om några sekunder.',
  'error.tooMany': 'För många försök. Vänta en stund och försök igen.',
  'error.tooManyWait': 'För många försök. Försök igen om {wait}.',
  // Both forms read the same in English today; the other languages need the split.
  'error.waitMinutes': { one: '{count} minut', other: '{count} minuter' },
  'error.waitSeconds': { one: '{count} sekund', other: '{count} sekunder' },
  'error.fields': 'Kontrollera fälten och försök igen.',
  'error.server': 'Något gick fel på servern. Försök igen om en stund.',
  'error.network': 'Det gick inte att nå servern. Kontrollera din anslutning.',
  'error.generic': 'Det fungerade inte. Försök igen.',

  // Outcome codes the OAuth callback puts in ?oauth= of /signin, /signup and /account.
  'oauth.invalid': 'Inloggningen kunde inte slutföras. Försök igen.',
  'oauth.denied': 'Inloggningen avbröts hos leverantören.',
  'oauth.session_mismatch': 'Du blev utloggad eller bytte konto under inloggningen. Logga in igen.',
  'oauth.account_exists':
    'Ett konto med den e-postadressen finns redan. Logga in på det med ditt lösenord och koppla sedan leverantören från din kontosida.',
  'oauth.invite_required':
    'Du behöver en inbjudan för att skapa ett konto. Skriv in din inbjudningskod nedan och försök igen.',
  'oauth.invite_invalid': 'Den inbjudan är inte längre giltig. Be om en ny.',
  'oauth.identity_taken': 'Det leverantörskontot är redan kopplat till ett annat classbeat-konto.',
  'oauth.identity_mismatch': 'Det är inte det leverantörskonto som är kopplat till ditt classbeat-konto.',
  'oauth.reauth_use_password':
    'Ditt konto har ett lösenord, så Google kan inte bekräfta att det är du. Bekräfta med ditt lösenord. Inget har ändrats.',
  'oauth.reauth_expired': 'Bekräftelsen tog längre tid än 5 minuter, så inget har ändrats. Försök igen.',
  'oauth.reauth_stale':
    'Leverantören bekräftade inte att du har loggat in igen de senaste 5 minuterna, så inget har ändrats.',
  'oauth.reauth_stale_password':
    'Leverantören bekräftade inte att du har loggat in igen de senaste 5 minuterna, så inget har ändrats. Använd ditt lösenord i stället.',
  'oauth.reauth_stale_set_password':
    'Leverantören bekräftade inte att du har loggat in igen de senaste 5 minuterna, så inget har ändrats. Om du vill ange ett lösenord loggar du ut och väljer ”Har du glömt ditt lösenord?” på inloggningssidan. Bekräfta sedan med det.',
  'oauth.reauth_stale_both':
    'Leverantören bekräftade inte att du har loggat in igen de senaste 5 minuterna, så inget har ändrats. Använd ditt lösenord i stället, om du har ett. Om du vill ange ett lösenord loggar du ut och väljer ”Har du glömt ditt lösenord?” på inloggningssidan. Bekräfta sedan med det.',
  'oauth.no_account':
    'Inget classbeat-konto är kopplat till {account}. Skapa ett konto för att gå med i betan, som bara är för inbjudna.',
  'oauth.providerAccount': '{provider}-kontot',
  'oauth.providerAccountOther': 'leverantörskontot',
  // Server sentences by code (ADR 0023 section 6): the text equals the backend's English; the
  // page words a code from here and shows the server's text for a code it does not have.
  'sentence.spotify.invalid_grant': 'Spotify godkänner inte längre anslutningen. Anslut Spotify igen.',
  'sentence.spotify.app_misconfigured':
    'Spotify känner inte igen den här appinställningen. Kontrollera Client ID och omdirigerings-URI:n i din Spotify-app.',
  'sentence.spotify.not_allowlisted':
    'Spotify avvisade det här kontot. Lägg till kontots namn och e-postadress under User Management i din Spotify-app (utvecklarläget tillåter 5 personer) och se till att appens ägare har Spotify Premium.',
  'sentence.spotify.forbidden': 'Spotify avvisade begäran för ditt konto eller din spellista.',
  'sentence.spotify.rate_limited': {
    one: 'Spotify ber oss sakta ner; försöker igen om cirka {count} minut.',
    other: 'Spotify ber oss sakta ner; försöker igen om cirka {count} minuter.',
  },
  'sentence.spotify.quota_exhausted': {
    one: 'Din Spotify-app har förbrukat sin kvot för tillfället. Försök igen om cirka {count} timme.',
    other: 'Din Spotify-app har förbrukat sin kvot för tillfället. Försök igen om cirka {count} timmar.',
  },
  'sentence.spotify.upstream_unavailable': 'Spotify svarar inte.',
  'sentence.spotify.not_found': 'Spotify hittade inte den spellistan.',
  'sentence.spotify.scope_mismatch':
    'Spotify gav inte den åtkomst som classbeat behöver. Anslut igen och tillåt den.',
  'sentence.spotify.invalid_response':
    'Spotify gav inte den åtkomst som classbeat behöver. Anslut igen och tillåt den.',
  'sentence.spotify.account_changed':
    'Det här är ett annat Spotify-konto än tidigare. Bekräfta för att byta (classbeat glömmer arbetsspellistan).',
  'sentence.spotify.access_denied': 'Spotify anslöts inte.',
  'sentence.spotify.unknown': 'Något gick fel med Spotify. Försök igen senare.',
  'sentence.job.consent_missing': 'Tillåt arbetsspellistan i dina Spotify-inställningar först.',
  'sentence.job.pool_unusable':
    'Din arbetsspellista har ändrats: gör den privat igen eller ställ in den på nytt.',
  'sentence.job.pool_missing': 'Din arbetsspellista saknades i Spotify; försöker igen av sig själv.',
  'sentence.job.pool_not_synced': 'MOWL har inte hunnit ikapp med arbetsspellistan; försök igen om en minut.',
  'sentence.job.playlist_mismatch':
    'Spotifys spellista stämde inte med de godkända låtarna; inget utformades. Skapa den igen.',
  'sentence.job.pool_too_small':
    'Det hittades inte tillräckligt många användbara låtar; prova en bredare stil eller epok.',
  'sentence.job.account_changed':
    'Ditt Spotify-konto är inte det som de här låtarna valdes för. Be om nya låtar.',
  'sentence.job.pool_busy': 'Ett annat låtval pågår fortfarande.',
  'sentence.job.media_host_not_allowed':
    'Mixen lades till i ditt MOWL-konto, men den kunde inte analyseras: det går inte att analysera en ny Swedebeat-mix på den här servern ännu. Mixar som analyserats tidigare fungerar fortfarande.',
  'sentence.job.media_refused': 'Ljudet i den här mixen kunde inte hämtas. Inget utformades.',
  'sentence.job.analysis_failed': 'Ljudet i den här mixen kunde inte analyseras. Prova en annan mix.',
  'sentence.job.feature_off': 'Den här funktionen är avstängd på den här servern just nu. Inget utformades.',
  'sentence.job.analysis_limit':
    'Du har analyserat 10 nya Swedebeat-mixar de senaste 24 timmarna. Mixar som analyserats tidigare fungerar fortfarande; prova en ny i morgon.',
  'sentence.job.handoff_expired':
    'Den kontrollen har gått ut. Börja om från Swedebeat-steget; inget hämtades.',
  'sentence.swedebeat.wait': {
    one: 'Swedebeat ber oss sakta ner; försöker igen om cirka {count} minut.',
    other: 'Swedebeat ber oss sakta ner; försöker igen om cirka {count} minuter.',
  },
  'sentence.swedebeat.stopped': 'Swedebeat ber oss sakta ner. Den här matchningen har stoppats.',

  // Class page, my classes, versions, save, undecided writes and pattern notes (ML5c). The class
  // text itself (names, cues, descriptions, song titles) is generated and stays as written.
  'class.tryAgain': 'Försök igen',
  'class.checkNeeded': 'Kontroll behövs',
  'class.checkAgain': 'Kontrollera igen',
  'class.doItAnyway': 'Gör det ändå',
  'class.confirm': 'Bekräfta',
  'class.cancel': 'Avbryt',
  'class.goToAccount': 'Gå till ditt konto',
  'class.waitRunning': 'Vänta först tills den pågående uppgiften är klar.',
  'class.runDropped': 'Anslutningen till servern bröts. Försöker igen.',
  'class.runStopping': 'Stoppar. Det tar en stund.',
  'class.runWaiting': 'Väntar på en ledig plats.',
  'class.versions': 'Versioner',
  'class.version': 'Version {number}',
  'class.useThisVersion': 'Använd den här versionen',
  'class.useVersionAria': 'Använd den här versionen {number}',
  'class.view': 'Visa',
  'class.viewing': 'Visas',
  'class.viewVersionAria': 'Visa version {number}',
  'class.viewingVersionAria': 'Visar version {number}',
  'class.fromVersion': 'från version {number}',
  'class.current': 'Aktuell',
  'class.column.length': 'Längd',
  'class.column.zone': 'Zon',
  'class.column.incline': 'Lutning',
  'class.column.cadence': 'Kadens',
  'class.column.position': 'Position',
  'class.column.cue': 'Instruktion',
  'class.position.standing': 'Stående',
  'class.position.seated': 'Sittande',
  'class.position.standingLower': 'stående',
  'class.position.seatedLower': 'sittande',
  'class.kind.warmup': 'Uppvärmning',
  'class.kind.main': 'Huvuddel',
  'class.kind.recovery': 'Återhämtning',
  'class.kind.cooldown': 'Nedvarvning',
  'class.cadence.beat': 'i takt med musiken',
  'class.cadence.range': '{low} till {high} rpm',
  'class.zone.short': 'Z{zone}',
  'class.zone.line': 'Z{zone} {name}',
  'class.unknownBpm':
    'MOWL kände ännu inte till tempot på de här låtarna, så kadensen på dem är en gissning:',

  // Reasons the class API refuses with (ADR 0007 sections 2, 3 and 12).
  'class.reason.stale_base':
    'Det här passet ändrades i en annan flik eller ett annat fönster, så din begäran skickades inte. Sidan har laddats om. Läs den aktuella versionen och fråga igen.',
  'class.reason.hash_mismatch':
    'Den versionen är inte den som den här sidan visade: den ändrades någon annanstans. Sidan har laddats om.',
  'class.reason.class_busy':
    'Det här passet är upptaget med en annan uppgift, kanske från en annan flik. Sidan har laddats om. Vänta tills uppgiften är klar och försök sedan igen.',
  'class.reason.revision_limit':
    'Det här passet har nått det högsta antal ändringar som ett pass kan ha. Behåll den version du gillar, eller utforma ett nytt pass.',
  'class.reason.not_changeable':
    'Det här passet har ingen egen musik, så det kan inte ändras via chatten. Du kan fortfarande visa, använda och spara dess versioner.',
  'class.reason.chat_full':
    'Den här konversationen är full. Behåll den version du gillar, eller utforma ett nytt pass.',
  'class.reason.not_found': 'Det här passet, eller den versionen, finns inte längre. Sidan har laddats om.',
  'class.reason.not_connected': 'MOWL är inte anslutet. Anslut det på din kontosida och fråga sedan igen.',
  'class.reason.invalid_input': 'Den begäran kunde inte användas. Kontrollera den och försök igen.',
  'class.reason.call_may_be_running':
    'classbeat kanske fortfarande skriver det här passet till MOWL, och om du raderar det nu försvinner den enda posten om den skrivningen. Vänta några minuter och försök sedan radera det igen.',
  'class.reason.music_unlinked':
    'Musiken i den här versionen finns inte i ditt anslutna MOWL-konto. Ändra den och spara den nya versionen.',
  'class.reason.running_not_evaluated':
    'Löppass kan inte ändras i din plan just nu, så din begäran skickades inte och inget debiterades. Försök igen senare.',
  'class.reason.no_beat_data':
    'Den här versionen har ingen beat-data att korrigera. Be först om en ändring via chatten och försök sedan igen.',
  'class.reason.correction_limit':
    'Det här passet har så många tidskorrigeringar som det kan rymma. Återställ några och försök sedan igen.',
  'class.reason.nothing_pending': 'Den aktuella versionen använder redan de här tidskorrigeringarna.',
  'class.reason.refit_limit':
    'Tidsinställningen för det här passet har korrigerats så många gånger det går. Utforma ett nytt pass för att börja om.',
  'class.reason.changedMeanwhile': 'Det här passet ändrades under tiden. Sidan har laddats om.',
  'class.origin.design_draft': 'Första utformningen',
  'class.origin.revise_draft': 'Ändrad på begäran',
  'class.origin.other': 'Version',
  'class.change.segments': 'Segment: {added} tillagda, {removed} borttagna.',
  'class.change.intervals': 'Intervaller: {added} tillagda, {removed} borttagna, {changed} ändrade.',
  'class.change.lengthSame': 'Längden är oförändrad, {length}.',
  'class.change.length': 'Längd: {from} till {to}.',
  'class.change.tssSame': 'TSS är oförändrat, {tss}.',
  'class.change.tss': 'TSS: {from} till {to}.',
  'class.change.text': 'Namn, beskrivning eller instruktioner ändrades.',

  // Save, Update and the undecided write (ADR 0007 sections 5 to 8 and 12).
  'class.saveReason.write_unresolved':
    'En tidigare sparning eller uppdatering av det här passet i MOWL är fortfarande oavgjord. Avgör den först med Kontrollera igen eller Gör det ändå i rutan nedan och försök sedan igen.',
  'class.saveReason.call_may_be_running':
    'classbeat kanske fortfarande skriver det här passet till MOWL. Vänta några minuter och försök sedan igen.',
  'class.saveReason.hash_mismatch':
    'Den versionen är inte den som den här sidan visade: den ändrades någon annanstans. Sidan har laddats om.',
  'class.saveReason.class_busy':
    'Det här passet är upptaget med en annan uppgift, kanske från en annan flik. Sidan har laddats om. Vänta tills uppgiften är klar och försök sedan igen.',
  'class.saveReason.not_connected':
    'MOWL är inte anslutet. Anslut det på din kontosida och försök sedan igen.',
  'class.saveReason.music_unlinked':
    'Musiken i den här versionen finns inte i ditt anslutna MOWL-konto. Ändra den och spara den nya versionen.',
  'class.saveReason.invalid_input':
    'Det gick inte att spara. Sidan har laddats om; kontrollera passet och försök igen.',
  'class.saveReason.busy': 'Du har redan uppgifter som körs. Vänta tills en är klar och försök sedan igen.',
  'class.saveReason.idempotency_mismatch':
    'Den begäran har redan skickats med annat innehåll. Ladda om sidan och försök igen.',
  'class.saveFailure.upstream_unavailable':
    'Det gick inte att nå MOWL. Inget skrevs. Försök igen om några minuter.',
  'class.saveFailure.rate_limited':
    'MOWL bad classbeat att sakta ner. Inget skrevs. Vänta några minuter och försök igen.',
  'class.saveFailure.lease_lost':
    'Arbetet avbröts för många gånger. Om classbeat inte kunde avgöra om något nådde MOWL står det i listan nedan så att du kan avgöra det. Annars skrevs inget; försök igen.',
  'class.saveFailure.not_connected':
    'MOWL är inte anslutet. Anslut det på din kontosida och försök sedan igen.',
  'class.saveFailure.needs_reconnect':
    'MOWL godkänner inte längre den sparade inloggningen. Anslut det igen på din kontosida och försök sedan igen.',
  'class.saveFailure.key_unavailable':
    'Den sparade MOWL-inloggningen kunde inte läsas. Anslut MOWL igen på din kontosida.',
  'class.saveFailure.invalid_input':
    'Den här versionen kunde inte skrivas till MOWL. Ladda om sidan och försök igen.',
  'class.saveFailure.operation_busy':
    'En annan uppgift av dig använde fortfarande MOWL. Inget skrevs. Försök igen om en stund.',
  'class.saveFailure.music_unlinked':
    'Musiken i den här versionen finns inte i ditt anslutna MOWL-konto. Ändra den och spara den nya versionen.',
  'class.saveFailure.program_changed':
    'Det här träningspasset ändrades i MOWL efter att classbeat sparade det. Inget skrevs.',
  'class.saveFailure.program_missing':
    'Det här träningspasset finns inte längre i ditt MOWL-konto. Inget skrevs.',
  'class.saveFailure.program_other_sport':
    'Det här träningspasset i MOWL är för en annan sport än det här passet, så classbeat ersatte det inte. Inget skrevs.',
  'class.saveFailure.internal': 'Något gick fel hos oss. Försök igen senare.',
  'class.saveFailure.default':
    'Jobbet blev inte klart. Titta i rutan nedan efter något som är oavgjort och försök sedan igen.',
  'class.uncertain.save.1':
    'classbeat bad MOWL att lägga till det här passet som ett nytt träningspass och fick inget tydligt svar.',
  'class.uncertain.save.2':
    'Träningspasset kan finnas i ditt MOWL-konto, helt eller bara delvis, eller så finns det inte alls. classbeat försöker inte igen av sig själv, så att du inte av misstag får två kopior.',
  'class.uncertain.update.1':
    'classbeat bad MOWL att uppdatera träningspasset som sparades tidigare och fick inget tydligt svar.',
  'class.uncertain.update.2':
    'Din MOWL-kopia kan vara oförändrad, uppdaterad eller bara delvis uppdaterad. MOWL kan också ha skapat ett nytt, tomt träningspass med samma namn under Mina träningspass; om du hittar ett är det ditt eget privata träningspass och du kan radera det i MOWL. classbeat försöker inte igen av sig själv.',
  'class.warning.duplicate':
    'Gör det ändå betyder att du avgör att träningspasset inte sparades. Om det ändå sparades kan det här lägga till ett andra träningspass i ditt MOWL-konto; du kan radera extra kopior i MOWL-appen.',
  'class.warning.overwrite':
    'Gör det ändå betyder att du avgör att uppdateringen inte skedde. Din MOWL-kopia kan vara delvis uppdaterad; nästa uppdatering ersätter den med den här versionen.',
  'class.outcome.save.found':
    'classbeat hittade träningspasset i ditt MOWL-konto och noterade det som sparat.',
  'class.outcome.save.nothing':
    'classbeat kontrollerade och träningspasset var inte sparat. Du kan spara det igen nu.',
  'class.outcome.save.still_unsure': 'classbeat kan fortfarande inte avgöra om träningspasset sparades.',
  'class.outcome.save.decided':
    'Uppfattat. Du kan spara det igen nu; om det ändå sparades får du två kopior i MOWL.',
  'class.outcome.update.found': 'classbeat såg att ditt MOWL-träningspass var uppdaterat och noterade det.',
  'class.outcome.update.nothing':
    'classbeat kontrollerade och ditt MOWL-träningspass är oförändrat. Du kan uppdatera det igen nu.',
  'class.outcome.update.still_unsure':
    'classbeat kan fortfarande inte avgöra om träningspasset uppdaterades.',
  'class.outcome.update.decided':
    'Uppfattat. Nästa uppdatering ersätter din MOWL-kopia med den här versionen.',
  'class.saved': 'Sparat. Träningspasset finns i ditt MOWL-konto, privat.',
  'class.savedSoundless': 'Sparat utan musik. Träningspasset finns i ditt MOWL-konto, privat.',
  'class.updated': 'Uppdaterat. Ditt MOWL-träningspass motsvarar nu den här versionen.',
  'class.updatedUnverified':
    'Uppdateringen gick igenom, men classbeat kunde inte bekräfta att MOWL nu stämmer överens (det kan fortfarande ha en spellista som du lade till i appen). Kontrollera träningspasset i MOWL-appen.',

  // Pattern notes (ADR 0017 section 4); the sentences mirror the backend's rationales.
  'class.rule.warmup_short':
    'De flesta pass av det här slaget cyklar några minuter före den första hårda insatsen.',
  'class.rule.hard_start_late':
    'De flesta pass av det här slaget når den första hårda insatsen före den här punkten.',
  'class.rule.cooldown_short':
    'De flesta pass av det här slaget slutar med en lätt nedvarvning på ett par minuter.',
  'class.rule.cooldown_long': 'De flesta pass av det här slaget håller nedvarvningen kort.',
  'class.rule.hard_share_low':
    'De flesta pass av det här slaget tillbringar en god del av turen i hårda zoner.',
  'class.rule.hard_share_high':
    'De flesta pass av det här slaget lämnar utrymme för lättare cykling mellan insatserna.',
  'class.rule.zone_ceiling_exceeded':
    'De flesta pass av det här slaget håller sig borta från de högsta zonerna.',
  'class.rule.top_zone_share_low':
    'De flesta pass av det här slaget innehåller en del cykling i de högsta zonerna.',
  'class.rule.top_zone_share_high': 'De flesta pass av det här slaget begränsar andelen i de högsta zonerna.',
  'class.rule.mean_zone_low':
    'De flesta pass av det här slaget har en jämnare ansträngningsnivå överlag än det här.',
  'class.rule.mean_zone_high':
    'De flesta pass av det här slaget har en lägre ansträngningsnivå överlag än det här.',
  'class.rule.tss_rate_low':
    'De flesta pass av det här slaget lägger lite mer träningsbelastning på varje minut.',
  'class.rule.tss_rate_high':
    'De flesta pass av det här slaget lägger lite mindre träningsbelastning på varje minut.',
  'class.rule.hard_too_early':
    'De flesta pass av det här slaget håller tillbaka de hårda insatserna under första kvarten.',
  'class.rule.standing_long': 'De flesta pass av det här slaget begränsar andelen stående cykling.',
  'class.rule.longest_interval_short': 'De flesta pass av det här slaget har minst ett längre intervall.',
  'class.rule.longest_interval_long':
    'De flesta pass av det här slaget har alla intervaller kortare än så här.',
  'class.rule.median_interval_short':
    'De flesta pass av det här slaget använder längre intervaller än så här.',
  'class.rule.median_interval_long':
    'De flesta pass av det här slaget använder kortare intervaller än så här.',
  'class.rule.sprints_few': 'De flesta pass av det här slaget lägger in några fler sprintar.',
  'class.rule.sprints_many': 'De flesta pass av det här slaget lägger in färre sprintar.',
  'class.rule.climb_share_low':
    'De flesta pass av det här slaget lägger mer av turen på sittande klättringar.',
  'class.rule.climb_share_high':
    'De flesta pass av det här slaget lägger mindre av turen på sittande klättringar.',
  'class.rule.work_rest_low': 'De flesta pass av det här slaget arbetar hårdare för varje minut vila.',
  'class.rule.work_rest_high': 'De flesta pass av det här slaget vilar längre för varje minut hårt arbete.',
  'class.rule.intensity_factor_low':
    'De flesta pass av det här slaget har en högre intensitet överlag än det här.',
  'class.rule.intensity_factor_high':
    'De flesta pass av det här slaget har en lägre intensitet överlag än det här.',
  'class.notes.hereOnly': 'Det här passet: {here}',
  'class.notes.hereUsual': 'Det här passet: {here}. Vanligt: {usual}',
  'class.notes.between': '{low} till {high}',
  'class.notes.orMore': '{low} eller mer',
  'class.notes.upTo': 'upp till {high}',
  'class.notes.perMinute': '{value} TSS per minut',
  'class.notes.title': 'Mönsteranteckningar',
  'class.notes.intro':
    'Vad som är vanligt i pass av det här slaget. Din beskrivning går först; de här anteckningarna hindrar aldrig något.',
  'class.notes.hidden': 'Anteckningen är dold.',
  'class.notes.showAgain': 'Visa igen',
  'class.notes.showAgainAria': 'Visa igen: {note}',
  'class.notes.dismiss': 'Avfärda',
  'class.notes.dismissAria': 'Avfärda: {note}',
  'class.notes.dismissFailed': 'Anteckningen kunde inte avfärdas. Försök igen om en stund.',
  'class.notes.showAgainFailed': 'Anteckningen kunde inte visas igen. Försök igen om en stund.',
  'class.notes.matches': 'Stämmer med din beskrivning',
  'class.notes.worth': 'Värt att titta på',
  'class.notes.good': 'Bra att veta',
  'class.notes.explained': 'Din beskrivning ber om detta, så det är bara en anteckning.',
  'class.notes.currentRules': 'Anteckningarna följer de aktuella reglerna',

  // The class card, my classes and the class page.
  'class.card.tss': 'TSS {value}',
  // The screen-reader label of the small class profile (MiniProfile). One form: English says "intervals" for every count.
  'class.profile.label': 'Passprofil, {length}, {count} intervaller',
  'class.profile.empty': 'Ingen passprofil ännu',
  'class.list.title': 'Mina pass',
  'class.list.design': 'Utforma ett pass',
  'class.list.loading': 'Laddar dina pass',
  'class.list.failed': 'Dina pass kunde inte laddas.',
  'class.list.none': 'Inga pass ännu',
  'class.list.noneText': 'Beskriv ett pass så utformar classbeat det utifrån din spellista.',
  'class.list.show': 'Visa',
  'class.list.filterAll': 'Alla',
  'class.list.filterSaved': 'Sparade i MOWL',
  'class.list.filterDrafts': 'Utkast',
  'class.list.search': 'Sök bland dina pass',
  'class.list.noMatch': 'Inget pass matchar. Ändra filtret eller sökningen.',
  'class.page.deletedTitle': 'Passet är raderat',
  'class.page.deleted': 'Passet, dess versioner och din konversation är borta från classbeat.',
  'class.page.loadingTitle': 'Ett ögonblick',
  'class.page.loading': 'Laddar ditt pass',
  'class.page.notFoundTitle': 'Passet hittades inte',
  'class.page.notFound': 'Det här passet finns inte, eller så är det inte ditt.',
  'class.page.failedTitle': 'Passet kunde inte laddas',
  'class.page.failed': 'Det gick inte att nå servern, eller så svarade den inte som väntat.',
  'class.page.running': 'Löpning',
  'class.page.noMusic': 'Ingen musik',
  'class.page.changeSongs': 'Byt låtar',
  'class.page.moreActions': 'Fler åtgärder',
  'class.page.deleteClass': 'Radera pass',
  'class.page.job.waiting': 'Låtarna väntar på ditt godkännande.',
  'class.page.job.design':
    'Det här passet håller på att utformas. Det kan ta några minuter, och du kan lämna sidan.',
  'class.page.job.refit': 'Dina tidskorrigeringar tillämpas.',
  'class.page.job.songs': 'Det arbetas med låtarna i det här passet.',
  'class.page.job.review': 'Granska låtarna',
  'class.page.job.progress': 'Se hur det går',
  'class.page.playlist': 'Din Spotify-spellista: {name}',
  'class.page.openSpotify': 'Öppna i Spotify',
  'class.page.noVersion': 'Det här passet har ingen version ännu.',
  'class.page.notCurrent':
    'Du tittar på version {viewing}. Passet använder version {current}, och ändringar utgår från den.',
  'class.page.back': 'Tillbaka till version {number}',
  'class.page.loadingVersion': 'Laddar versionen',
  'class.page.versionFailed': 'Den här versionen kunde inte laddas. Ladda om sidan och försök igen.',
  'class.side.label': 'Spara, ändra och versioner',
  'class.songStrip.label': 'Låtar',
  'class.songStrip.title': '{title}, {bpm} BPM',

  // Delete class (ADR 0007 section 11).
  'class.delete.title': 'Radera pass',
  'class.delete.uncertain':
    'classbeat är inte säker på om en sparning av det här passet i MOWL gick igenom. Om du raderar passet nu försvinner den enda posten om det.',
  'class.delete.warning':
    'Det här raderar passet, alla dess versioner och din konversation från classbeat. Det går inte att ångra.',
  'class.delete.kept': {
    one: 'Det {count} träningspass som classbeat sparade i ditt MOWL-konto blir kvar där; radera det i MOWL.',
    other:
      'De {count} träningspass som classbeat sparade i ditt MOWL-konto blir kvar där; radera dem i MOWL.',
  },
  'class.delete.keptNone':
    'Alla träningspass som classbeat sparade i ditt MOWL-konto blir kvar där; radera dem i MOWL.',
  'class.delete.anyway': 'Radera ändå',
  'class.delete.confirm': 'Ja, radera det här passet',
  'class.delete.keep': 'Behåll det',

  // Duplicate a class.
  'class.duplicate.label': 'Duplicera',
  'class.duplicate.trigger': '{label} {title}',
  'class.duplicate.title': 'Duplicera det här passet',
  'class.duplicate.descriptionVersion':
    'Version {number} blir version 1 av ett nytt pass. Det är inte sparat i MOWL och har ännu inga godkända låtar eller någon konversation. Det räknas som ett pass den här månaden.',
  'class.duplicate.descriptionThis':
    'Den här versionen blir version 1 av ett nytt pass. Det är inte sparat i MOWL och har ännu inga godkända låtar eller någon konversation. Det räknas som ett pass den här månaden.',
  'class.duplicate.name': 'Kopians namn',
  'class.duplicate.sending': 'Duplicerar…',

  // Words the class-page helpers return (lib/profile.ts, lib/classCards.ts, api/classHistory.ts, api/classDuplicate.ts; ML5e).
  'class.zoneName.recovery': 'Återhämtning',
  'class.zoneName.endurance': 'Uthållighet',
  'class.zoneName.tempo': 'Tempo',
  'class.zoneName.threshold': 'Tröskel',
  'class.zoneName.vo2max': 'VO2max',
  'class.zoneName.anaerobic': 'Anaerob',
  'class.zoneName.neuromuscular': 'Neuromuskulär',
  'class.incline.raised': 'Lutning',
  'class.incline.flat': 'Platt',
  'class.tss.approximate': 'ungefärligt: zon 7 har ingen exakt TSS-vikt',
  'class.length.minutes': '{minutes} min',
  'class.length.hoursMinutes': '{hours} h {minutes} min',
  'class.status.designing': 'Utformas',
  'class.status.changing': 'Ändras',
  'class.status.busy': 'Upptaget',
  'class.status.noVersion': 'Ingen version ännu',
  'class.status.unsavedChanges': 'Version {number} har ändringar som inte finns i MOWL ännu',
  'class.status.saved': 'Version {number} sparad i MOWL',
  'class.status.notSaved': 'Version {number}, inte sparad i MOWL',
  'class.compare.gone': 'En av de här versionerna finns inte längre.',
  'class.compare.unreadable':
    'En av de här versionerna kan inte längre läsas, så de två går inte att jämföra.',
  'class.compare.failed': 'De två versionerna kunde inte jämföras. Försök igen.',
  'class.asked.duplicated': 'Duplicerat från ”{title}”',
  'class.asked.duplicatedVersion': 'Duplicerat från ”{title}”, version {number}',
  'class.asked.duplicatedGone': 'Duplicerat från ett pass som inte finns kvar',
  'class.asked.request': 'Du bad om: ”{request}”',
  'class.asked.changed': 'Ändrad på begäran',
  'class.asked.songs': 'Låtar ändrade',
  'class.asked.first': 'Första utformningen',
  'class.compare.segments': 'Segment: {added} tillagda, {removed} borttagna.',
  'class.compare.intervalsChanged': { one: '{count} intervall ändrat', other: '{count} intervaller ändrade' },
  'class.compare.intervalsAdded': { one: '{count} intervall tillagt', other: '{count} intervaller tillagda' },
  'class.compare.intervalsRemoved': {
    one: '{count} intervall borttaget',
    other: '{count} intervaller borttagna',
  },
  'class.compare.intervals': '{parts}.',
  'class.compare.lengthLonger': 'Längd: {from} till {to} ({diff} längre).',
  'class.compare.lengthShorter': 'Längd: {from} till {to} ({diff} kortare).',
  'class.compare.lengthSame': 'Längd: oförändrad, {length}.',
  'class.compare.tssSame': 'TSS: oförändrat, {tss}.',
  'class.compare.tss': 'TSS: {from} till {to}.',
  'class.compare.timing': 'Tidsvarningar: {from} till {to}.',
  'class.compare.musicSame': 'Musik: densamma.',
  'class.compare.musicSongs': 'Musik: samma källa, men låtarna ändrades.',
  'class.compare.musicDifferent': 'Musik: annan musik.',
  'class.compare.onlyText': 'Bara namn, beskrivningar eller instruktioner skiljer sig åt.',
  'class.compare.same': 'Träningspassen är likadana.',
  'class.compare.textToo': 'Vissa namn, beskrivningar eller instruktioner skiljer sig också åt.',
  'class.duplicate.copyOf': '{title} (kopia)',
  'class.duplicate.reason.not_ready':
    'Det här passet har ingen färdig utformning ännu, så det finns inget att kopiera. Försök igen när det är klart.',
  'class.duplicate.reason.feature_off':
    'Pass som det här kan inte skapas just nu, så det går inte att kopiera.',
  'class.duplicate.reason.soundless_not_evaluated':
    'Pass utan musik är inte tillgängliga i din plan ännu, så det här går inte att kopiera.',
  'class.duplicate.reason.not_changeable':
    'Det här passet har ingen egen musik, så det kan inte ändras via chatten. Du kan fortfarande visa, använda och spara dess versioner.',
  'class.duplicate.reason.hash_mismatch':
    'Den versionen är inte den som den här sidan visade: den ändrades någon annanstans. Ladda om sidan och försök igen.',
  'class.duplicate.reason.not_found': 'Det här passet, eller den versionen, finns inte längre.',
  'class.duplicate.reason.invalid_input':
    'Det namnet kan inte användas. Ge kopian ett namn på 1 till 100 tecken.',
  'class.duplicate.reason.idempotency_mismatch':
    'Den här kopian har redan begärts med andra uppgifter. Stäng det här och försök igen.',
  'class.duplicate.reason.internal': 'Den här versionen kan inte längre läsas, så den går inte att kopiera.',
  // Shown on the account page and on the undecided-attach panel (components/account/mowlWarning.ts).
  'account.forgetWarning':
    'classbeat glömmer vilka spellistor det har lagt till i din MOWL-musik, och alla tillägg som lämnats oavgjorda. Nästa pass du utformar utifrån en av de spellistorna kan lägga till den i din MOWL-musik en gång till, så att du kan se två kopior där.',

  // Draft summary and revision view.
  'class.draft.summary': 'Utkast {number}, {total} totalt',
  'class.revision.heading': 'Version {number}, {origin}',
  'class.revision.length': 'Längd',
  'class.revision.tss': 'TSS',
  'class.revision.segments': 'Segment',
  'class.revision.songs': 'Låtar',
  'class.revision.hardest': 'Hårdaste zon',
  'class.revision.musicChanged':
    'Musiken ändrades sedan versionen före: spellistan i MOWL är inte den som det här passet byggdes på, så kontrollera att låtarna fortfarande passar.',
  'class.revision.songBySong': 'Låt för låt',
  'class.zoneTime.title': 'Tid i varje zon',
  'class.chart.summary':
    'Ansträngning över tid för {title}: {segments} segment, {total} totalt. Den hårdaste zonen är {zone}.',
  'class.chart.summaryRunning':
    'Ansträngning över tid för {title}: {segments} segment, {total} totalt. Den hårdaste zonen är {zone}. Lutningen för varje intervall visas när du markerar det.',
  'class.chart.keys': '{title}: använd vänster- och högerpilarna för att titta på varje intervall',
  'class.chart.minutes': '{minutes} min',
  'class.chart.select':
    'Markera en stapel, eller fokusera diagrammet och använd piltangenterna, för att se ett intervall.',
  'class.chart.zoneName': 'Zon {zone} {name}',
  'class.chart.range': '{from} till {to}',

  // Versions and their history.
  'class.revisions.none': 'Det finns ingen version ännu.',
  // One form on purpose: the English wording is "{count} segments" for every count, so no plural is needed.
  'class.revisions.segments': '{count} segment',
  'class.history.unavailable': 'Versionshistoriken kunde inte läsas, så bara den enkla listan visas.',
  'class.history.pick': 'Välj två versioner för att jämföra dem.',
  'class.history.cannotChange':
    'Ändra det är inte tillgängligt för det här passet just nu: det har ingen egen musik, eller så är den sortens pass avstängt. Du kan fortfarande visa, jämföra, använda och spara dess versioner.',
  'class.history.unreadable': {
    one: '{count} tidigare version kan inte längre läsas och visas inte i listan.',
    other: '{count} tidigare versioner kan inte längre läsas och visas inte i listan.',
  },
  'class.history.list': 'Versionshistorik',
  'class.history.savedMowl': 'Sparad i MOWL',
  'class.history.savedEarlier': 'Sparad i en tidigare MOWL-anslutning',
  'class.history.fromSongs': 'Godkända låtar',
  'class.history.tss': 'TSS {value}',
  'class.history.compare': 'Jämför',
  'class.history.compareAria': 'Jämför version {number}',
  'class.history.compareButton': 'Jämför versionerna {older} och {newer}',
  'class.history.compareTitle': 'Version {older} till version {newer}',

  // Beat 1 corrections (ADR 0007 amendment "beat-one corrections").
  'class.beat.asAnalysed': 'som analyserad',
  'class.beat.later': { one: '{count} beat senare', other: '{count} beat senare' },
  'class.beat.earlier': { one: '{count} beat tidigare', other: '{count} beat tidigare' },
  'class.beat.song': 'Låt {song}',
  'class.beat.title': 'Beat 1',
  'class.beat.intro':
    'Lyssna på passet. Om ändringarna i en låt kommer ett beat för tidigt flyttar du dess beat 1 senare; om de kommer för sent flyttar du det tidigare. En takt har fyra beat, så ett steg är en fjärdedels takt.',
  'class.beat.from': 'Från {start}, {bpm} BPM',
  'class.beat.bpmChanged': 'Tempot på den här låten ändrades sedan du ställde in dess beat 1: lyssna igen.',
  'class.beat.estimate': 'Beat 1 är en uppskattning här: lyssna på den första ändringen i den här låten.',
  'class.beat.group': 'Beat 1 för {name}',
  'class.beat.moveEarlier': 'Flytta beat 1 för {name} tidigare',
  'class.beat.moveLater': 'Flytta beat 1 för {name} senare',
  'class.beat.reset': 'Återställ beat 1 för {name}',
  'class.beat.earlierButton': 'Tidigare',
  'class.beat.laterButton': 'Senare',
  'class.beat.resetButton': 'Återställ',
  'class.beat.stale':
    'De här tidskorrigeringarna gäller musik som den här versionen inte spelar: den analyserades på nytt eller ändrades, eller så finns låten inte i den här versionen. De sparas och räknas inte mot gränsen, så om du gör en tidigare version aktuell kan de användas igen. Rensa dem om du inte behöver dem längre.',
  'class.beat.staleRow': 'Låt {song}: {beats}',
  'class.beat.clearAria': 'Rensa den tidigare tidskorrigeringen för låt {song}',
  'class.beat.clear': 'Rensa',
  'class.beat.clearAll': 'Rensa alla tidskorrigeringar',
  'class.beat.pending':
    'Den här versionen använder inte dina tidskorrigeringar ännu. Tillämpa dem för att skapa en ny version där ändringarna flyttats till de korrigerade beaten; spara eller uppdatera den i MOWL efteråt.',
  'class.beat.apply': 'Tillämpa tidskorrigeringar',

  // The Change it chat (ADR 0007 section 3).
  'class.chat.title': 'Ändra det',
  'class.chat.noLonger': 'Den här ändringen finns inte längre.',
  'class.chat.addingPlaylist': 'Lägger till din spellista i din MOWL-musik.',
  'class.chat.stop': 'Sluta ändra',
  'class.chat.unfinished': 'Den här begäran blev inte klar. Inget ändrades.',
  'class.chat.checking': 'Kontrollerar vad som hände',
  'class.chat.askAgain': 'Du kan be om ändringen igen nu.',
  'class.chat.changeSongs': 'Byt låtar',
  'class.chat.stopped': 'Den här ändringen stoppades. Inget ändrades.',
  'class.chat.empty':
    'Inget har efterfrågats ännu. Säg vad som ska ändras, så ändrar classbeat det här passet.',
  'class.chat.requests': 'Dina önskemål',
  'class.chat.youAsked': 'Du bad om',
  'class.chat.changed': 'classbeat ändrade det',
  'class.chat.changedVersion': 'classbeat ändrade det, version {number}',
  'class.chat.musicChanged': 'Musiken ändrades: spellistan i MOWL är inte den som var där förut.',
  'class.chat.designerNote': 'Anteckning från utformaren:',
  'class.chat.viewVersion': 'Visa version {number}',
  'class.chat.writeSomething': 'Skriv vad du vill ändra.',
  'class.chat.tooLong': 'En begäran får vara högst {max} tecken; den här har {count}.',
  'class.chat.noVersion': 'Det finns ingen version att ändra ännu.',
  'class.chat.unavailable':
    'Ändra det är inte tillgängligt för det här passet just nu. Du kan fortfarande visa, jämföra, använda och spara dess versioner.',
  'class.chat.busy':
    'Det här passet är upptaget med en annan uppgift. Du kan be om en ändring när den är klar.',
  'class.chat.form': 'Be om en ändring',
  'class.chat.label': 'Vad ska ändras?',
  'class.chat.hint':
    'Till exempel: gör den andra klättringen längre, eller lägg till en sprint före nedvarvningen. Högst {max} tecken.',

  // Save to MOWL and Update (ADR 0007 sections 5 to 9).
  'class.save.title': 'Spara i MOWL',
  'class.save.runSave': 'Sparar i MOWL:',
  'class.save.runUpdate': 'Uppdaterar i MOWL:',
  'class.save.stop': 'Stoppa',
  'class.save.checking': 'Uppdaterat. Kontrollerar vad MOWL behöll…',
  'class.save.uncertain':
    'classbeat kunde inte avgöra om MOWL tog emot det. Läs rutan nedan och avgör sedan.',
  'class.save.updateAnyway': 'Uppdatera ändå (ersätter dina ändringar i MOWL)',
  'class.save.stopped': 'Det här stoppades innan något skrevs, eller så listar classbeat det nedan.',
  'class.save.detachedLine':
    'Det här passet sparades tidigare i en tidigare MOWL-anslutning. Det träningspasset är inte längre kopplat, så det kan inte uppdateras. Spara lägger till ett nytt i ditt nuvarande MOWL-konto.',
  'class.save.notSaved': 'Inte sparat i MOWL ännu.',
  'class.save.upToDate': 'Sparat i MOWL som version {saved}. Det är aktuellt.',
  'class.save.behind':
    'Sparat i MOWL som version {saved}. Den aktuella versionen ({current}) har ändringar som inte är sparade ännu.',
  'class.save.detachedOther':
    'Ett annat träningspass sparades tidigare i en tidigare MOWL-anslutning och är inte längre kopplat.',
  'class.save.noVersion': 'Det finns ingen version att spara ännu.',
  'class.save.lost':
    'classbeat kan inte längre läsa den uppgiften, så det kan inte avgöra hur den slutade. Raden ovan visar vad MOWL har för det här passet nu; kontrollera den innan du försöker igen.',
  'class.save.again': 'Spara igen',
  'class.save.asNew': 'Spara som ett nytt träningspass',
  'class.save.updateAgain': 'Uppdatera igen',
  'class.save.update': 'Uppdatera sparat träningspass',
  'class.save.confirmSave':
    'classbeat lägger till version {number} av det här passet i ditt MOWL-konto som ett nytt träningspass. Det är privat: bara du kan se det.',
  'class.save.confirmSaveSoundless':
    'classbeat lägger till version {number} av det här passet i ditt MOWL-konto som ett nytt träningspass utan musik. Det är privat: bara du kan se det.',
  'class.save.confirmSaveMore':
    'Ett nytt träningspass läggs till; det du sparade tidigare blir kvar som det är.',
  'class.save.confirmUpdate':
    'classbeat läser först träningspasset i ditt MOWL-konto. Om du har ändrat det i MOWL efter att classbeat sparade det stannar classbeat och skriver ingenting. Annars ersätter det träningspasset med version {number}. Det förblir privat, i ditt MOWL-konto.',
  'class.save.confirmUpdateSoundless':
    'classbeat läser först träningspasset i ditt MOWL-konto. Om du har ändrat det i MOWL efter att classbeat sparade det stannar classbeat och skriver ingenting. Annars ersätter det träningspasset med version {number}, utan musik. Det förblir privat, i ditt MOWL-konto.',
  'class.save.overwriteTitle': 'Uppdatera ändå?',
  'class.save.overwriteText':
    'Det här ersätter träningspasset i ditt MOWL-konto med version {number}, inklusive alla ändringar du gjort i det i MOWL, eller vad en skrivning som inte blev klar lämnade där. Det går inte att ångra. Träningspasset förblir privat.',
  'class.save.updateButton': 'Uppdatera träningspass',
  'class.save.replace': 'Ja, ersätt det',
  'class.save.updateUnavailable':
    'Det går inte att uppdatera det sparade träningspasset ännu. Spara lägger i stället till ett nytt träningspass.',

  // Undecided writes and the undecided playlist attach (ADR 0005 section 7, ADR 0007 section 8).
  'class.write.title.save': 'classbeat kunde inte avgöra om ditt träningspass sparades',
  'class.write.title.update': 'classbeat kunde inte avgöra om ditt träningspass uppdaterades',
  'class.write.waiting':
    'classbeat kanske fortfarande skickar det här till MOWL, så Kontrollera igen och Gör det ändå väntar till {time}.',
  'class.write.running':
    'classbeat kanske fortfarande skriver till MOWL, så det går inte att avgöra ännu. Vänta några minuter.',
  'class.write.settled': 'Det var redan avgjort. Listan har uppdaterats.',
  'class.write.notSaved': 'Ja, det sparades inte',
  'class.write.notUpdated': 'Ja, det uppdaterades inte',
  'class.write.loadFailed': 'Det som återstår att avgöra för det här passet kunde inte laddas.',
  'class.write.heading': 'Oklart vad som nådde MOWL',
  'class.attach.title': 'classbeat kunde inte avgöra om din spellista lades till',
  'class.attach.intro':
    'För att läsa din musik bad classbeat MOWL att lägga till spellistan du valde i din MOWL-musik. MOWL svarade inte tydligt, så spellistan kan finnas där nu eller inte. classbeat försöker inte igen av sig själv, så att du inte av misstag får två kopior.',
  'class.attach.found': 'Spellistan finns i din MOWL-musik. Du kan utforma passet igen nu.',
  'class.attach.absent': 'Spellistan finns inte i din MOWL-musik. Du kan utforma passet igen nu.',
  'class.attach.still_unsure': 'classbeat kan fortfarande inte avgöra om spellistan lades till.',
  'class.attach.decided':
    'Uppfattat. Nästa utformning lägger till spellistan i din MOWL-musik igen, vilket kan lämna två kopior där.',
  'class.attach.settled': 'Det var redan avgjort. Listan nedan är aktuell.',
  'class.attach.loadFailed': 'Det som återstår att avgöra kunde inte laddas.',
  'class.attach.none':
    'classbeat har inget steg kvar för dig att avgöra här, så det kan inte erbjuda Kontrollera igen eller Gör det ändå. Om du vill vara säker på att spellistan inte läggs till två gånger tittar du i din MOWL-musik. För att rensa classbeats post om den kopplar du bort MOWL och ansluter det igen på {account} och startar sedan en ny utformning från formuläret.',
  'class.attach.accountPage': 'din kontosida',
  'class.attach.earlier': 'Det här steget kommer från en tidigare utformning av dig.',
  'class.attach.warning':
    'Gör det ändå betyder att du avgör att spellistan inte lades till. Nästa utformning lägger till den i din MOWL-musik igen. Om den ändå lades till får du två kopior av den där.',
  'class.attach.addAgain': 'Ja, lägg till den igen',
  'common.tryAgain': 'Försök igen',
  'newclass.title': 'Utforma ett pass',
  'newclass.loading': 'Laddar din MOWL-musik',
  'newclass.noMowl.reconnect':
    'MOWL godkänner inte längre den sparade inloggningen. {link} och kom sedan tillbaka hit.',
  'newclass.noMowl.connect': 'classbeat läser din musik från MOWL. {link} och kom sedan tillbaka hit.',
  'newclass.noMowl.reconnectLink': 'Anslut MOWL igen',
  'newclass.noMowl.connectLink': 'Anslut MOWL på din kontosida',
  'newclass.playlistError.unavailable': 'Det gick inte att nå MOWL. Försök igen om några minuter.',
  'newclass.playlistError.rateLimited': 'MOWL bad classbeat att sakta ner. Försök igen om några minuter.',
  'newclass.playlistError.default':
    'Dina spellistor kunde inte laddas från MOWL. Försök igen om några minuter.',
  'newclass.createError.not_connected': 'MOWL är inte anslutet. Anslut det först på din kontosida.',
  'newclass.createError.class_busy': 'Det här passet har redan en utformning som pågår.',
  'newclass.createError.idempotency_mismatch': 'Det fungerade inte. Kontrollera formuläret och försök igen.',
  'newclass.createError.feature_off':
    'Löppass är inte tillgängliga just nu. Byt till Cykling eller försök igen senare.',
  'newclass.createError.soundless_not_evaluated':
    'Pass utan musik är inte klara för din plan ännu. Välj en spellista eller försök igen senare.',
  'newclass.createError.running_not_evaluated':
    'Löppass är inte tillgängliga i din plan ännu. Inget debiterades. Byt till Cykling eller försök igen senare.',
  'newclass.createError.invalid_input':
    'Passet godkändes inte. Kontrollera fälten; stilen och beskrivningen tillsammans kan vara över {bytes} byte.',
  'newclass.createError.soundlessOff':
    'Pass utan musik är inte tillgängliga just nu. Välj en spellista eller försök igen senare.',
  'newclass.planMaxLength': 'Din plan tillåter pass på upp till {count} minuter.',
  'newclass.planLeavesOut': 'Din plan omfattar inte {list}.',
  'newclass.planLeavesOut.races': 'race',
  'newclass.planLeavesOut.ftpTests': 'FTP-test',
  'newclass.list.and': '{head} och {last}',
  'newclass.invalid.name': 'Ge passet ett namn på högst 100 tecken.',
  'newclass.invalid.playlist': 'Välj en spellista.',
  'newclass.invalid.mix': 'Välj en Swedebeat-mix.',
  'newclass.invalid.length': 'Längden är 10 till 180 minuter.',
  'newclass.invalid.climbs': 'Antalet klättringar är 0 till 10.',
  'newclass.invalid.sprints': 'Antalet sprintar är 0 till 20.',
  'newclass.invalid.races': 'Antalet race är 0 till 5.',
  'newclass.invalid.style': 'Stilen får vara högst 60 tecken.',
  'newclass.invalid.notes': 'Beskrivningen får vara högst 2000 tecken.',
  'newclass.invalid.briefTooLong':
    'Stilen och beskrivningen är för långa för ett pass (gränsen är 4096 byte totalt, och bokstäver med accent eller utanför det latinska alfabetet räknas som mer än en). Korta ner dem och försök igen.',
  'newclass.minutes': '{count} min',
  'newclass.notSet': 'Inte angiven',
  'newclass.untitledMix': 'Namnlös mix',
  'newclass.untitledPlaylist': 'Namnlös spellista',
  'newclass.summary.length': 'Längd',
  'newclass.summary.music': 'Musik',
  'newclass.summary.noMusic': 'Ingen musik',
  'newclass.summary.songsPicked': 'Låtar som väljs åt dig',
  'newclass.summary.noMix': 'Ingen mix vald ännu',
  'newclass.summary.noPlaylist': 'Ingen spellista vald ännu',
  'newclass.noPlaylists.title': 'Inga spellistor hittades',
  'newclass.noPlaylists.body':
    'MOWL listar inga Spotify-spellistor för ditt konto. Lägg till en i Intelligent Cycling-appen och ladda sedan om den här sidan.',
  'newclass.formLabel': 'Nytt pass',
  'newclass.basics': 'Grunderna',
  'newclass.sport': 'Sport',
  'newclass.sport.cycling': 'Cykling',
  'newclass.sport.running': 'Löpning',
  'newclass.sport.runningHint':
    'Ett löppass utformas utifrån en spellista som redan finns i ditt MOWL-konto. Varje intervall har en ansträngningszon från 1 till 5 och en inställning för plant eller lutning.',
  'newclass.className': 'Passets namn',
  'newclass.classLength': 'Passets längd',
  'newclass.commonLengths': 'Vanliga längder',
  'newclass.otherLength': 'Annan längd',
  'newclass.lengthMinutes': 'Längd i minuter',
  'newclass.music': 'Musik',
  'newclass.theRun': 'Löpturen',
  'newclass.theRide': 'Cykelturen',
  'newclass.climbs': 'Klättringar',
  'newclass.climbs.fewer': 'Färre klättringar',
  'newclass.climbs.more': 'Fler klättringar',
  'newclass.sprints': 'Sprintar',
  'newclass.sprints.fewer': 'Färre sprintar',
  'newclass.sprints.more': 'Fler sprintar',
  'newclass.races': 'Race',
  'newclass.races.fewer': 'Färre race',
  'newclass.races.more': 'Fler race',
  'newclass.stepper.range': '{min} till {max}',
  'newclass.ftp': 'FTP-test',
  'newclass.ftp.none': 'Inget',
  'newclass.ftp.minutes': '{count} minuter',
  'newclass.notes': 'Något mer?',
  'newclass.notes.hint': 'Berätta för classbeat hur passet ska kännas.',
  'newclass.notes.hintNoGames': 'Berätta för classbeat hur passet ska kännas. Spel ingår inte i din plan.',
  'newclass.side.title': 'Ditt pass hittills',
  'newclass.side.sketchNote': 'En grov skiss av dina siffror. Claude utformar det riktiga passet.',
  'newclass.side.classesLeft': 'Använder 1 av dina {count} återstående pass den här månaden',
  'newclass.side.noClassesLeft': 'Du har inga pass kvar den här månaden.',
  'newclass.side.submit': 'Utforma mitt pass',
  'newclass.sketch': 'En grov skiss av ditt pass',
  'newclass.sketch.none': 'Ingen skiss ännu',
  'newclass.effort': 'Ansträngning',
  'newclass.effort.easy': 'Lätt',
  'newclass.effort.moderate': 'Måttlig',
  'newclass.effort.hard': 'Hård',
  'newclass.mix.none':
    'MOWL listar inga Swedebeat-mixar för ditt konto ännu. Skapa en mix i Swedebeat-appen, låt den synkas till Intelligent Cycling och ladda sedan om den här sidan.',
  'newclass.mix.label': 'Swedebeat-mix',
  'newclass.mix.analysed': 'Analyserad tidigare',
  'newclass.mix.hint':
    'classbeat lägger till den här mixen i din MOWL-musik så att den kan analysera ljudet och läsa låtarna. Den första analysen kan ta ungefär en minut.',
  'newclass.playlist.label': 'Spellista',
  'newclass.playlist.hint':
    'classbeat lägger till den här spellistan i din MOWL-musik så att den kan läsa låtarna. Den förblir privat för dig.',
  'newclass.style.suggestions': 'Stilförslag',
  'newclass.style.label': 'Stil',
  'newclass.style.hint': 'Till exempel: backar, intervaller, uthållighet.',
  'newclass.style.80s': '80-tal',
  'newclass.style.90s': '90-tal',
  'newclass.style.2000s': '2000-tal',
  'newclass.style.pop': 'Pop',
  'newclass.style.rock': 'Rock',
  'newclass.style.dance': 'Dans',
  'newclass.style.hiphop': 'Hiphop',
  'newclass.style.latin': 'Latin',
  'job.failure.upstream_unavailable.title': 'Det gick inte att nå MOWL',
  'job.failure.upstream_unavailable.detail':
    'classbeat försökte flera gånger. Ditt pass utformades inte. Försök igen om några minuter.',
  'job.failure.rate_limited.title': 'MOWL bad classbeat att sakta ner',
  'job.failure.rate_limited.detail': 'Ditt pass utformades inte. Vänta några minuter och försök igen.',
  'job.failure.model_unavailable.title': 'Utformaren är upptagen',
  'job.failure.model_unavailable.detail':
    'Tjänsten som utformar passet kunde inte ta emot begäran. Försök igen om några minuter.',
  'job.failure.lease_lost.title': 'Utformningen avbröts',
  'job.failure.lease_lost.detail': 'Den avbröts för många gånger. Försök igen.',
  'job.failure.operation_busy.title': 'En annan utformning använder den här spellistan',
  'job.failure.operation_busy.detail':
    'En annan utformning av dig lade fortfarande till samma spellista i MOWL. Försök igen om en stund.',
  'job.failure.not_connected.title': 'MOWL är inte anslutet',
  'job.failure.not_connected.detail': 'Anslut MOWL på din kontosida och börja sedan om.',
  'job.failure.needs_reconnect.title': 'MOWL godkänner inte längre den sparade inloggningen',
  'job.failure.needs_reconnect.detail': 'Anslut MOWL igen på din kontosida och börja sedan om.',
  'job.failure.key_unavailable.title': 'Den sparade MOWL-inloggningen kunde inte läsas',
  'job.failure.key_unavailable.detail': 'Anslut MOWL igen på din kontosida och börja sedan om.',
  'job.failure.invalid_input.title': 'Begäran kunde inte användas',
  'job.failure.invalid_input.detail': 'Kontrollera formuläret och börja om.',
  'job.failure.account_disabled.title': 'Ditt konto kan inte starta utformningar just nu',
  'job.failure.account_disabled.detail': 'Kontakta personen som bjöd in dig.',
  'job.failure.model_refused.title': 'Utformaren avböjde den här beskrivningen',
  'job.failure.model_refused.detail': 'Ändra beskrivningen och börja om.',
  'job.failure.model_config.title': 'classbeat kan inte utforma pass just nu',
  'job.failure.model_config.detail': 'Det här är ett problem hos oss. Försök igen senare.',
  'job.failure.invalid_output.title': 'Utformaren gjorde inget användbart pass',
  'job.failure.invalid_output.detail': 'Försök igen, eller förenkla beskrivningen.',
  'job.failure.limit_exceeded.title': 'Den här beskrivningen krävde mer arbete än en utformning får använda',
  'job.failure.limit_exceeded.detail': 'Prova ett kortare eller enklare pass.',
  'job.failure.outbound_blocked.title': 'Något gick fel hos oss',
  'job.failure.outbound_blocked.detail': 'Försök igen senare.',
  'job.failure.program_changed.title': 'Det här träningspasset ändrades i MOWL',
  'job.failure.program_changed.detail':
    'Det här träningspasset ändrades i MOWL efter att classbeat sparade det.',
  'job.failure.program_missing.title': 'Det här träningspasset finns inte längre i MOWL',
  'job.failure.program_missing.detail': 'Det här träningspasset finns inte längre i ditt MOWL-konto.',
  'job.failure.music_changed.title': 'Musiken ändrades',
  'job.failure.music_changed.detail':
    'Spellistan eller mixen är inte den som den här versionen gjordes på, så tidskorrigeringarna tillämpades inte. Be om en ändring via chatten för att utforma efter musiken som den är nu.',
  'job.failure.program_other_sport.title': 'Det här träningspasset är för en annan sport',
  'job.failure.program_other_sport.detail':
    'Det här träningspasset i MOWL är för en annan sport än det här passet, så classbeat ersatte det inte.',
  'job.failure.use_change_songs.title': 'Den här begäran gäller bara låtarna',
  'job.failure.use_change_songs.detail': 'Inget ändrades. Använd Byt låtar på passidan för att välja andra.',
  'job.failure.media_host_not_allowed.title': 'Nya Swedebeat-mixar kan inte analyseras här ännu',
  'job.failure.media_host_not_allowed.detail':
    'Mixen lades till i ditt MOWL-konto, men den kunde inte analyseras. Mixar som du analyserat tidigare fungerar fortfarande. Prova en av dem, eller en annan sorts musik.',
  'job.failure.media_refused.title': 'Ljudet från den här mixen kunde inte hämtas',
  'job.failure.media_refused.detail': 'Inget utformades. Försök igen senare.',
  'job.failure.analysis_failed.title': 'Ljudet från den här mixen kunde inte analyseras',
  'job.failure.analysis_failed.detail': 'Prova en annan mix.',
  'job.failure.feature_off.title': 'Det här är avstängt just nu',
  'job.failure.feature_off.detail': 'Inget utformades. Försök igen senare.',
  'job.failure.analysis_limit.title': 'För många nya Swedebeat-mixar i dag',
  'job.failure.analysis_limit.detail':
    'Du har analyserat 10 nya mixar de senaste 24 timmarna. Mixar som analyserats tidigare fungerar fortfarande; prova en ny i morgon.',
  'job.failure.soundless_not_evaluated.title': 'Pass utan musik är inte klara för din plan ännu',
  'job.failure.soundless_not_evaluated.detail':
    'Inget utformades. Välj en spellista i stället, eller försök igen senare.',
  'job.failure.music_unlinked.title': 'Musiken i den här versionen finns inte i ditt MOWL-konto',
  'job.failure.music_unlinked.detail':
    'Musiken i den här versionen finns inte i ditt anslutna MOWL-konto. Ändra den och spara den nya versionen.',
  'job.failure.soundlessInvalid.title': 'Begäran kunde inte användas',
  'job.failure.soundlessInvalid.detail':
    'Ett pass utan musik är för cykling och har inga låtar eller någon mix. Kontrollera formuläret och börja om.',
  'job.failure.fallback.title': 'Utformningen blev inte klar',
  'job.failure.fallback.detail': 'Försök igen senare.',
  'job.failure.featureOff.soundless.title': 'Pass utan musik är avstängda just nu',
  'job.failure.featureOff.soundless.detail':
    'Inget utformades. Välj en spellista i stället, eller försök igen senare.',
  'job.failure.featureOff.swedebeat.title': 'Swedebeat är avstängt just nu',
  'job.failure.featureOff.swedebeat.detail':
    'Inget utformades. Försök igen senare, eller använd en annan sorts musik.',
  'job.failure.featureOff.running.title': 'Löppass är avstängda just nu',
  'job.failure.featureOff.running.detail':
    'Inget utformades. Försök igen senare, eller utforma ett cykelpass.',
  'job.stage.music': 'Läser din spellista',
  'job.stage.design': 'Bygger ditt pass',
  'job.stage.revise': 'Ändrar passet',
  'job.stage.prepare': 'Kontrollerar passet och ditt MOWL-konto',
  'job.stage.read': 'Läser träningspasset i MOWL',
  'job.stage.save': 'Skriver till MOWL',
  'job.stage.verify': 'Kontrollerar vad MOWL behöll',
  'job.stage.fit': 'Flyttar ändringarna till de korrigerade beaten',
  'job.stage.default': 'Kommer igång',
  'job.step.pool_search': 'Hittar låtar som passar',
  'job.step.pool_write': 'Sparar din arbetsspellista',
  'job.step.pool_analyse': 'MOWL lyssnar på musiken',
  'job.step.choose': 'Väljer de bästa låtarna till varje del',
  'job.step.prepare': 'Kontrollerar dina konton',
  'job.step.refitMusic': 'Läser din musik',
  'job.retry.upstream_unavailable': 'MOWL svarade inte. Försöker igen om en liten stund.',
  'job.retry.rate_limited': 'MOWL bad classbeat att sakta ner. Försöker igen om en liten stund.',
  'job.retry.model_unavailable': 'Utformaren är upptagen. Försöker igen om en liten stund.',
  'job.retry.default': 'Något gick fel. Försöker igen om en liten stund.',
  'job.mixStage': 'Hämtar och analyserar din mix',
  'job.waitingForSlot': 'Väntar på en ledig plats. Andra mixar analyseras före din.',
  'job.waitingToStart': 'Väntar på en ledig plats.',
  'job.youCanLeave':
    'Du kan lämna den här sidan. Ditt pass fortsätter, och du kan komma tillbaka till den här adressen eller hitta passet under Mina pass.',
  'job.use.changeSongs':
    'Den här begäran gäller bara låtarna, så inget ändrades. Använd Byt låtar för att välja andra.',
  'job.use.newClassForSongs':
    'Den här begäran gäller bara låtarna, så inget ändrades. Det här passet använder en egen spellista; för att använda annan musik utformar du ett nytt pass.',
  'job.use.otherMusicRunning':
    'Den här begäran gäller bara musiken, så inget ändrades. Ett löppass använder en spellista från ditt MOWL-konto och kan inte byta låtar; för att använda annan musik utformar du ett nytt pass.',
  'job.use.finishSongs':
    'Den här begäran gäller bara låtarna, så inget ändrades. Slutför eller försök om låtvalet på den här sidan först och använd sedan Byt låtar.',
  'job.use.newClassForMusicSoundless':
    'Den här begäran gäller bara musiken, så inget ändrades. Det här passet har ingen musik; för att lägga till musik utformar du ett nytt pass med en spellista.',
  'job.revise.upstream_unavailable':
    'Det gick inte att nå MOWL. Inget ändrades. Försök igen om några minuter.',
  'job.revise.rate_limited':
    'MOWL bad classbeat att sakta ner. Inget ändrades. Vänta några minuter och försök igen.',
  'job.revise.model_unavailable': 'Utformaren är upptagen. Inget ändrades. Försök igen om några minuter.',
  'job.revise.lease_lost': 'Ändringen avbröts för många gånger. Inget ändrades. Försök igen.',
  'job.revise.operation_busy':
    'En annan utformning av dig använde fortfarande samma spellista. Inget ändrades. Försök igen om en stund.',
  'job.revise.not_connected': 'MOWL är inte anslutet. Anslut det på din kontosida och fråga sedan igen.',
  'job.revise.needs_reconnect':
    'MOWL godkänner inte längre den sparade inloggningen. Anslut det igen på din kontosida och fråga sedan igen.',
  'job.revise.key_unavailable':
    'Den sparade MOWL-inloggningen kunde inte läsas. Anslut MOWL igen på din kontosida.',
  'job.revise.invalid_input': 'Den begäran kunde inte användas. Inget ändrades. Formulera dig annorlunda.',
  'job.revise.account_disabled': 'Ditt konto kan inte ändra pass just nu. Kontakta personen som bjöd in dig.',
  'job.revise.model_refused': 'Utformaren avböjde den här begäran. Inget ändrades. Formulera dig annorlunda.',
  'job.revise.model_config':
    'classbeat kan inte ändra pass just nu. Det här är ett problem hos oss. Försök igen senare.',
  'job.revise.invalid_output':
    'Utformaren gjorde ingen användbar ändring, eller en som inte ändrar något. Inget ändrades. Försök igen, eller formulera dig annorlunda.',
  'job.revise.limit_exceeded':
    'Den här begäran krävde mer arbete än en ändring får använda. Inget ändrades. Be om något mindre.',
  'job.revise.outbound_blocked': 'Något gick fel hos oss. Inget ändrades. Försök igen senare.',
  'job.revise.music_unlinked':
    'Musiken i den här versionen finns inte i ditt anslutna MOWL-konto. Ändra den och spara den nya versionen.',
  'job.revise.soundless_not_evaluated':
    'Pass utan musik är inte klara för din plan ännu. Inget ändrades. Försök igen senare.',
  'job.revise.program_changed': 'Det här träningspasset ändrades i MOWL efter att classbeat sparade det.',
  'job.revise.program_missing': 'Det här träningspasset finns inte längre i ditt MOWL-konto.',
  'job.revise.program_other_sport':
    'Det här träningspasset i MOWL är för en annan sport än det här passet, så classbeat ersatte det inte.',
  'job.revise.soundlessFeatureOff':
    'Pass utan musik är avstängda just nu. Inget ändrades. Försök igen senare.',
  'job.revise.default': 'Ändringen blev inte klar. Inget ändrades. Försök igen senare.',
  'songs.stage.pool_search': 'Hittar låtar som kan passa',
  'songs.stage.pool_write': 'Skriver din arbetsspellista i Spotify',
  'songs.stage.pool_analyse': 'Väntar på att MOWL ska analysera låtarna',
  'songs.stage.choose': 'Väljer låtarna till ditt pass',
  'songs.stage.create': 'Skapar din spellista i Spotify',
  'songs.stage.fill': 'Lägger till de godkända låtarna i din spellista',
  'songs.stage.attach': 'Lägger till din spellista i din MOWL-musik',
  'songs.approve.expired': 'Låtarna gick ut innan de godkändes. Inget skrevs till ditt Spotify.',
  'songs.approve.superseded': 'En nyare lista med låtar har ersatt de här, så de kan inte godkännas längre.',
  'songs.approve.hash_mismatch':
    'Låtarna ändrades efter att den här sidan laddades, så inget godkändes. Ladda om sidan för att se den aktuella listan.',
  'songs.approve.not_connected':
    'Spotify eller MOWL är inte anslutet. Anslut det på din kontosida, kom sedan tillbaka och godkänn.',
  'songs.approve.account_changed': 'Ditt Spotify-konto är inte det som låtarna valdes för. Be om nya låtar.',
  'songs.approve.not_waiting':
    'De här låtarna kan inte godkännas längre. De är redan godkända eller stoppade.',
  'songs.approve.busy': 'Du har redan utformningar som körs. Vänta tills en är klar och godkänn sedan.',
  'songs.approve.quota': 'Du har använt upp det som din plan tillåter för tillfället.',
  'songs.approve.not_found': 'Låtarna hittades inte, eller så är de inte dina.',
  'songs.approve.invalid_input':
    'Godkännandet accepterades inte. Ladda om sidan för att se de aktuella låtarna.',
  'songs.approve.default': 'Det fungerade inte. Försök igen.',
  'songs.start.song_picking_off': 'Låtval är inte tillgängligt just nu.',
  'songs.start.consent_missing':
    'Tillåt arbetsspellistan i dina Spotify-inställningar på din kontosida först.',
  'songs.start.pool_busy':
    'Ett annat låtval pågår fortfarande. Vänta tills det är klart och försök sedan igen.',
  'songs.start.class_busy':
    'Det här passet har redan en uppgift som körs. Vänta tills den är klar och försök sedan igen.',
  'songs.start.stale_base': 'Passet ändrades under tiden. Ladda om sidan och försök igen.',
  'songs.start.revision_limit': 'Det här passet har nått det högsta antal versioner det kan ha.',
  'songs.start.chat_full': 'Konversationen för det här passet är full, så det kan inte ändras längre.',
  'songs.start.not_song_picked':
    'Musiken i det här passet valdes inte av Claude, så dess låtar kan inte ändras här.',
  'songs.start.invalid_input': 'Begäran accepterades inte. Kontrollera fälten och försök igen.',
  'songs.start.not_connected': 'Spotify eller MOWL är inte anslutet. Anslut det först på din kontosida.',
  'songs.start.receipt_invalid':
    'De här låtarna kan inte göras om till en spellista igen: de har redan använts eller aldrig godkänts. Välj låtar igen.',
  'songs.start.account_changed': 'Ditt Spotify-konto är inte det som låtarna valdes för. Välj låtar igen.',
  'songs.start.write_unresolved':
    'Ett tidigare steg på Spotify är fortfarande oavgjort. Avgör det först (Kontrollera igen eller Gör det ändå) och försök sedan igen.',
  'songs.start.idempotency_mismatch': 'Det fungerade inte. Kontrollera formuläret och försök igen.',
  'songs.start.default': 'Det fungerade inte. Försök igen.',
  'songs.failure.invalid_grant.title': 'Spotify godkänner inte längre anslutningen',
  'songs.failure.app_misconfigured.title': 'Spotify känner inte igen den här appinställningen',
  'songs.failure.not_allowlisted.title': 'Spotify avvisade det här kontot',
  'songs.failure.forbidden.title': 'Spotify avvisade begäran',
  'songs.failure.rate_limited.title': 'Spotify eller MOWL bad classbeat att sakta ner',
  'songs.failure.rate_limited.detail': 'Vänta några minuter och försök igen.',
  'songs.failure.quota_exhausted.title': 'Din Spotify-app har använt upp sin kvot',
  'songs.failure.quota_exhausted.detail':
    'Din Spotify-app har använt upp sin kvot för tillfället. Försök igen om några timmar.',
  'songs.failure.upstream_unavailable.title': 'Det gick inte att nå Spotify eller MOWL',
  'songs.failure.upstream_unavailable.detail':
    'classbeat försökte flera gånger. Försök igen om några minuter.',
  'songs.failure.not_found.title': 'En spellista finns inte längre i Spotify',
  'songs.failure.not_found.detail': 'Arbetsspellistan finns inte längre i Spotify. Börja om.',
  'songs.failure.scope_mismatch.title': 'Spotify gav inte den åtkomst som classbeat behöver',
  'songs.failure.account_changed.title': 'Det här är inte det Spotify-konto som låtarna valdes för',
  'songs.failure.access_denied.title': 'Spotify anslöts inte',
  'songs.failure.not_connected.title': 'Spotify eller MOWL är inte anslutet',
  'songs.failure.not_connected.detail': 'Anslut det på din kontosida och börja sedan om.',
  'songs.failure.consent_missing.title': 'Arbetsspellistan är inte tillåten ännu',
  'songs.failure.pool_unusable.title': 'Din arbetsspellista ändrades',
  'songs.failure.pool_missing.title': 'Din arbetsspellista fanns inte längre i Spotify',
  'songs.failure.pool_missing.detail': 'Din arbetsspellista fanns inte längre i Spotify. Försök igen.',
  'songs.failure.pool_not_synced.title': 'MOWL har inte hunnit ikapp ännu',
  'songs.failure.playlist_mismatch.title': 'Spotifys spellista stämde inte med de godkända låtarna',
  'songs.failure.playlist_mismatch.detail':
    'Spotifys spellista stämde inte med de godkända låtarna; inget utformades. Skapa den igen.',
  'songs.failure.pool_too_small.title': 'Det hittades inte tillräckligt många användbara låtar',
  'songs.uncertain.title': 'classbeat kunde inte avgöra om din spellista skrevs i Spotify',
  'songs.uncertain.text1':
    'Spotify svarade inte i tid medan classbeat skrev spellistan för dina godkända låtar. Spellistan kan finnas i ditt Spotify nu eller inte.',
  'songs.uncertain.text2':
    'Kontrollera igen frågar Spotify vad som finns där. Gör det ändå skriver utan att veta.',
  'songs.uncertain.overwriteWarning':
    'Om spellistan redan finns i ditt Spotify kan du få två spellistor med samma namn. Du kan radera den extra i Spotify. Gå bara vidare om du är säker på att den inte skrevs.',
  'songs.outcome.found':
    'Spellistan finns i ditt Spotify. Skapa spellistan igen för att gå vidare med ditt pass.',
  'songs.outcome.nothing':
    'Inget skrevs till Spotify. Skapa spellistan igen för att gå vidare med ditt pass.',
  'songs.outcome.decided':
    'Du bestämde att skriva den igen. Skapa spellistan igen för att gå vidare med ditt pass.',
  'songs.outcome.still_unsure':
    'Spotify ger fortfarande inget besked. Vänta några minuter och kontrollera igen.',
  'common.didNotWork': 'Det fungerade inte. Försök igen.',
  'songs.uncertain.mayStillRunning':
    'classbeat kanske fortfarande skriver till Spotify, så det går inte att avgöra ännu. Vänta några minuter.',
  'songs.uncertain.alreadySettled': 'Det var redan avgjort. Ladda om sidan.',
  'songs.uncertain.waiting':
    'classbeat kanske fortfarande skickar det här till Spotify, så Kontrollera igen och Gör det ändå väntar några minuter.',
  'songs.uncertain.confirm': 'Bekräfta',
  'songs.uncertain.confirmYes': 'Ja, den skrevs inte',
  'songs.uncertain.checkAgain': 'Kontrollera igen',
  'songs.uncertain.doAnyway': 'Gör det ändå',
  'job.steps.label': 'Steg',
  'job.steps.done': ' (klart)',
  'job.notFound.title': 'Passet hittades inte',
  'job.notFound.text': 'Den här utformningen finns inte, eller så är den inte din.',
  'job.notFound.designLink': 'Utforma ett pass',
  'job.signedOut.title': 'Du är utloggad',
  'job.signedOut.text': 'Logga in igen för att se den här utformningen. Den fortsätter under tiden.',
  'job.signedOut.signIn': 'Logga in',
  'job.moment.title': 'Ett ögonblick',
  'job.moment.unreachable': 'Det gick inte att nå servern. Försöker igen.',
  'job.moment.loading': 'Laddar',
  'job.dropped': 'Anslutningen till servern bröts. Försöker igen.',
  'job.cancelled.title': 'Du stoppade den här utformningen',
  'job.cancelled.detail': 'Inget sparades.',
  'job.expired.title': 'Den här utformningen gick ut',
  'job.expired.detail': 'Starta en ny.',
  'job.waitingForYou.title': 'Väntar på dig',
  'job.waitingForYou.text': 'Den här utformningen väntar på ett svar från dig.',
  'job.cancelError.finished': 'Den här utformningen blev precis klar.',
  'job.yourClass': 'ditt pass',
  'job.findingSongs': 'Hittar låtar till {name}…',
  'job.designing': 'Utformar {name}…',
  'job.stepOf': 'Steg {step} av {total}',
  'job.stopping': 'Stoppar. Det tar en stund.',
  'job.addingPlaylist': 'Lägger till din spellista i din MOWL-musik.',
  'job.toolCalls': 'Utformaren har kontrollerat sitt utkast {count} gång(er).',
  'job.stop': 'Sluta utforma',
  'job.ready.title': 'Ditt pass är klart',
  'job.ready.loadingDraft': 'Laddar utkastet',
  'job.ready.noDraft': 'Den här utformningen blev klar utan utkast.',
  'job.ready.draftFailed': 'Utkastet kunde inte laddas.',
  'job.ready.openClass': 'Öppna passet',
  'job.ready.designAnother': 'Utforma ett till pass',
  'job.again.notConnected':
    'MOWL är inte anslutet, eller anslöts på nytt sedan den här utformningen startade. Anslut det på din kontosida om det behövs och starta sedan en ny utformning.',
  'job.again.classBusy':
    'Det här passet har redan en utformning som pågår, kanske från en annan flik. Vänta tills den är klar och försök sedan igen.',
  'job.again.tooMany': 'Du har redan utformningar som körs. Vänta tills en är klar.',
  'job.again.design': 'Utforma igen',
  'job.again.startNew': 'Starta en ny utformning',
  'job.again.tryAgain': 'Försök igen',
  'job.matchStopped': 'Swedebeat-matchningen stoppades',
  'job.goToAccount': 'Gå till ditt konto',
  'songs.list.label': 'Låtar',
  'songs.list.openSpotify': 'ÖPPNA SPOTIFY',
  'songs.list.openSpotifyLabel': 'ÖPPNA SPOTIFY, {title}',
  'songs.list.bpm': '{bpm} BPM',
  'songs.list.new': 'Ny',
  'songs.list.replace': 'Byt ut',
  'songs.list.replaceLabel': 'Byt ut {title}',
  'music.label': 'Musik',
  'music.playlist': 'Använd min spellista',
  'music.pick': 'Välj låtar åt mig',
  'music.swedebeat': 'Använd min Swedebeat-mix',
  'music.none': 'Ingen musik',
  'music.needsSpotify': 'För att välja låtar behövs Spotify. {link} först.',
  'music.connectSpotify': 'Anslut Spotify',
  'music.needsConsent': 'Tillåt först arbetsspellistan i dina Spotify-inställningar. {link}',
  'music.openSettings': 'Öppna Spotify-inställningarna',
  'music.pickHint':
    'Claude föreslår låtar, kontrollerar dem mot MOWL:s BPM och längd och visar dig listan. Kandidaterna hamnar i en privat arbetsspellista i ditt Spotify; spellistan för ditt pass skapas först när du har godkänt låtarna.',
  'music.noneHint':
    'classbeat utformar passet utan spellista eller låtar: du får intervallerna, ansträngningarna och instruktionerna, och spelar din egen musik. Det sparas i MOWL utan musik.',
  'music.swedebeatHint':
    'classbeat utformar passet efter låtarna och beaten i din mix. Du behöver ingen Swedebeat-inloggning i classbeat för det här; MOWL listar mixarna i ditt Intelligent Cycling-konto.',
  'songs.change.writeRequest': 'Skriv vad du vill ha annorlunda med låtarna.',
  'songs.change.tooLong': 'Begäran får vara högst {max} tecken.',
  'songs.change.markSongs': 'Markera låtarna som ska bytas ut, eller byt ut alla.',
  'songs.change.notConnected': 'Spotify eller MOWL är inte anslutet. Anslut det först på din kontosida.',
  'songs.change.title': 'Byt låtar',
  'songs.change.wait': 'Vänta först tills den pågående uppgiften är klar.',
  'songs.change.loadFailed': 'Låtarna kunde inte laddas. Ladda om sidan.',
  'songs.change.loading': 'Laddar låtarna',
  'songs.change.intro':
    'Markera de låtar du vill byta ut. De andra låtarna blir kvar, och du godkänner den nya listan innan något skrivs till ditt Spotify.',
  'songs.change.different': 'Vad ska vara annorlunda?',
  'songs.change.marked': 'Byt de markerade låtarna',
  'songs.change.all': 'Byt alla låtar',
  'songs.ended.stoppedTitle': 'Du stoppade det här',
  'songs.ended.stoppedApproved': 'Du stoppade jobbet efter att ha godkänt låtarna. Passet blev inte klart.',
  'songs.ended.stoppedNothing': 'Inget skrevs till ditt Spotify.',
  'songs.ended.expiredApproved': 'Det här jobbet gick ut',
  'songs.ended.expiredSongs': 'De här låtarna gick ut',
  'songs.ended.expiredApprovedDetail': 'Jobbet tog för lång tid och avslutades.',
  'songs.ended.expiredSongsDetail': 'Ingen godkände låtarna i tid. Inget skrevs till ditt Spotify.',
  'songs.ended.loadFailed': 'Det som återstår att avgöra kunde inte laddas. Ladda om sidan.',
  'songs.ended.checking': 'Kontrollerar vad som hände',
  'songs.ended.nothingLeft': 'Det finns inget kvar att avgöra. Du kan skapa spellistan igen.',
  'songs.ended.reconnect': 'Anslut Spotify igen',
  'songs.ended.createAgain': 'Skapa spellistan igen',
  'songs.ended.pickAgain': 'Välj låtar igen',
  'songs.ended.startNew': 'Starta ett nytt pass',
  'songs.ended.backToClass': 'Tillbaka till passet',
  'songs.approval.privacy':
    'När du godkänner skapar classbeat en privat spellista med de här låtarna i ditt eget Spotify-konto. En privat spellista visas inte på din profil eller i sökningen, men alla som har länken till den kan öppna den.',
  'songs.approval.stopped':
    'Låtarna stoppades, men classbeat kunde inte läsa passet, så inget nytt startades. Tryck på Be om ändringar igen för att försöka en gång till.',
  'songs.approval.cannotChange': 'Låtarna kan inte ändras längre, så inget startades. Välj låtar igen.',
  'songs.approval.loading': 'Laddar låtarna',
  'songs.approval.loadFailedTitle': 'Låtarna kunde inte laddas',
  'songs.approval.loadFailed': 'Låtarna kunde inte laddas. Inget godkändes.',
  'songs.approval.approvedTitle': 'Du godkände de här låtarna',
  'songs.approval.approvedText': 'classbeat skapar din spellista och gör klart passet.',
  'songs.approval.refresh': 'Uppdatera',
  'songs.approval.supersededTitle': 'En nyare lista ersatte de här låtarna',
  'songs.approval.supersededText':
    'En nyare lista med låtar har ersatt de här, så de kan inte godkännas längre.',
  'songs.approval.expiredTitle': 'De här låtarna gick ut',
  'songs.approval.stoppedTitle': 'De här låtarna stoppades',
  'songs.approval.expiredText': 'Låtarna gick ut innan de godkändes. Inget skrevs till ditt Spotify.',
  'songs.approval.stoppedText': 'Låtarna stoppades. Inget skrevs till ditt Spotify.',
  'songs.approval.eyebrow': 'Spellista för godkännande',
  'songs.approval.heading': 'Låtar till ditt pass',
  'songs.approval.summary': '{count} låtar, {total} totalt. Du bad om {target}.',
  'songs.approval.omittedNoBpm': {
    one: '{count} låt utelämnades eftersom MOWL inte har någon BPM för den.',
    other: '{count} låtar utelämnades eftersom MOWL inte har någon BPM för dem.',
  },
  'songs.approval.expiry':
    'Godkänn senast {when}. Därefter går låtarna ut och inget skrivs till ditt Spotify.',
  'songs.approval.approve': 'Godkänn låtarna',
  'songs.approval.noteLabel': 'Vad ska vara annorlunda? (valfritt)',
  'songs.approval.markHint':
    'Markera låtarna som ska bytas ut och be sedan om ändringar. De andra låtarna blir kvar.',
  'songs.approval.askChanges': 'Be om ändringar',
  'songs.approval.stopAndChange': 'Stoppa och ändra begäran',
  'songs.approval.reload': 'Ladda om sidan',
  'songs.approval.openClass': 'Öppna passet',

  // Home, setup checklist, theme, dialog, unsubscribe page (ML5d).
  'home.welcome': 'Välkommen tillbaka',
  'home.welcomeName': 'Välkommen tillbaka, {name}',
  'home.design.title': 'Utforma ett nytt pass',
  'home.design.text': 'Beskriv det, så väljer classbeat låtarna och timar ansträngningarna efter musiken.',
  'home.design.presets': 'Börja från en förinställning',
  'home.design.button': 'Utforma ett pass',
  'home.recent': 'Senaste pass',
  'home.loading': 'Laddar dina pass',
  'home.loadFailed': 'Dina pass kunde inte laddas.',
  'home.empty': 'Inga pass ännu. Ditt första visas här.',
  'home.seeAll': 'Se alla dina pass',
  'setup.title': 'Kom igång',
  'setup.account': 'Skapa ditt konto',
  'setup.mowl': 'Anslut MOWL',
  'setup.mowlAgain': 'Anslut MOWL igen',
  'setup.spotify': 'Anslut Spotify (valfritt)',
  'setup.spotifyAgain': 'Anslut Spotify igen (valfritt)',
  'setup.action': 'Anslut',
  'setup.done': ', klart',
  'setup.notDone': ', inte klart ännu',
  'theme.label': 'Tema',
  'theme.dark': 'Mörkt',
  'theme.light': 'Ljust',
  'theme.system': 'Samma som enheten',
  'dialog.close': 'Stäng',
  'spotifyPage.back': 'Tillbaka till ditt konto',
  'unsub.title': 'Avsluta prenumeration',
  'unsub.needsLink': 'Den här sidan behöver länken från e-postmeddelandet.',
  'unsub.doneTitle': 'Du är avregistrerad',
  'unsub.doneAll': 'classbeat skickar inte längre valfri e-post till dig.',
  'unsub.doneCategory': 'classbeat skickar inte längre den här typen av e-post till dig.',
  'unsub.doneNote':
    'Länkar för inloggning, återställning av lösenord och bekräftelse skickas alltid. Du kan ändra vilken valfri e-post du får, inklusive säkerhetsaviseringar, under E-post på din kontosida.',
  'unsub.linkInvalid':
    'Länken är inte längre giltig. Använd länken i ett nyare e-postmeddelande, eller avsnittet E-post i ditt konto.',
  'unsub.intro': 'Välj vad som ska stoppas. Inget ändras förrän du trycker på en knapp.',
  'unsub.stopCategory': 'Stoppa den här typen av e-post',
  'unsub.stopAll': 'Stoppa all valfri e-post',

  // Music page.
  'music.page.title': 'Musik',
  'music.page.connectTitle': 'Anslut Spotify',
  'music.page.connectText':
    'Anslut Spotify så att classbeat kan välja låtar åt dig och skapa spellistor till dina pass.',
  'music.page.connectButton': 'Anslut Spotify',
  'music.page.madeTitle': 'Spellistor som classbeat har gjort',
  'music.page.madeLoading': 'Laddar dina spellistor',
  'music.page.madeFailed': 'Dina spellistor kunde inte laddas.',
  'music.page.madeEmpty':
    'Inga spellistor ännu. När du godkänner låtar till ett pass visas dess spellista här.',
  'music.page.mowlTitle': 'Dina spellistor i MOWL',
  'music.page.mowlLoading': 'Laddar din MOWL-musik',
  'music.page.mowlFailed':
    'Dina MOWL-spellistor kunde inte laddas. Anslut MOWL på {link}, eller försök igen om några minuter.',
  'music.page.mowlFailedLink': 'din kontosida',
  'music.page.mowlEmpty': 'MOWL listar inga Spotify-spellistor för ditt konto.',
  'music.page.untitled': 'Namnlös spellista',
  'music.page.forClass': 'För {link}',
  'music.page.openSpotify': 'ÖPPNA SPOTIFY',

  // Plan refusals (api/planMessages.ts) and month names.
  'month.long.1': 'januari',
  'month.long.2': 'februari',
  'month.long.3': 'mars',
  'month.long.4': 'april',
  'month.long.5': 'maj',
  'month.long.6': 'juni',
  'month.long.7': 'juli',
  'month.long.8': 'augusti',
  'month.long.9': 'september',
  'month.long.10': 'oktober',
  'month.long.11': 'november',
  'month.long.12': 'december',
  'plan.dayMonth': '{day} {month}',
  'plan.thisMonth': 'den här månaden',
  'plan.classes': { one: '{count} pass', other: '{count} pass' },
  'plan.designs': { one: '{count} utformning', other: '{count} utformningar' },
  'plan.feature.games': 'spel',
  'plan.feature.races': 'race',
  'plan.feature.ftp_tests': 'FTP-test',
  'plan.saveWays': 'Ändra det så att det passar, eller gör en tidigare version aktuell och spara sedan igen.',
  'plan.quota.length.save':
    'Din plan tillåter inte ett så långt pass, så den här versionen kan inte sparas. {ways}',
  'plan.quota.length.saveLimit':
    'Din plan tillåter pass på upp till {limit} minuter, och den här versionen är längre, så den kan inte sparas. {ways}',
  'plan.quota.length.version': 'Den versionen är längre än din plan tillåter. Välj en annan version.',
  'plan.quota.length.versionLimit':
    'Din plan tillåter pass på upp till {limit} minuter, och den versionen är längre. Välj en annan version.',
  'plan.quota.length.new': 'Din plan tillåter inte ett så långt pass. Välj en kortare längd.',
  'plan.quota.length.newLimit': 'Din plan tillåter pass på upp till {limit} minuter. Välj en kortare längd.',
  'plan.quota.feature.save':
    'Den här versionen använder något som din plan inte omfattar, så den kan inte sparas. {ways}',
  'plan.quota.feature.saveNamed':
    'Den här versionen använder {name}, vilket din plan inte omfattar, så den kan inte sparas. {ways}',
  'plan.quota.feature.version':
    'Den versionen använder något som din plan inte omfattar. Välj en annan version.',
  'plan.quota.feature.versionNamed':
    'Den versionen använder {name}, vilket din plan inte omfattar. Välj en annan version.',
  'plan.quota.feature.new': 'Din plan omfattar inte en del av det här passet. Ta bort den och försök igen.',
  'plan.quota.feature.newNamed': 'Din plan omfattar inte {name}. Ta bort dem och försök igen.',
  'plan.quota.saves.window.day': 'de senaste 24 timmarna',
  'plan.quota.saves.window.hour': 'den senaste timmen',
  'plan.quota.saves.later.day': 'Försök igen i morgon.',
  'plan.quota.saves.later.hour': 'Försök igen om en timme.',
  'plan.quota.saves':
    'Du har sparat till MOWL så många gånger som classbeat tillåter under {window}. {later}',
  'plan.quota.savesLimit':
    'Du har sparat eller uppdaterat {classes} i MOWL under {window}, vilket är så många som classbeat tillåter. {later}',
  'plan.quota.moreFrom': 'Fler från {day}.',
  'plan.quota.moreNextMonth': 'Fler i början av nästa månad.',
  'plan.quota.cost':
    'Du har använt det som din plan tillåter för {month}, så inga fler pass kan utformas just nu. {more}',
  'plan.quota.classesNoLimit': 'Du har använt dina pass för {month}. {more}',
  'plan.quota.classesUsed': 'Du har använt {used} av dina {classes} för {month}. {more}',
  'plan.quota.classesAll': 'Du har använt dina {classes} för {month}. {more}',
  'plan.busy.generic': 'Du har redan utformningar som körs. Vänta tills en är klar och försök sedan igen.',
  'plan.busy.limit':
    'Din plan tillåter {designs} åt gången, och så många körs. Vänta tills en är klar och försök sedan igen.',
  'plan.reason.code_invalid':
    'Koden är inte giltig. Kontrollera den och försök igen. Den kan ha gått ut eller redan vara förbrukad.',
  'plan.reason.already_redeemed': 'Du har redan använt den koden.',
  'plan.reason.trial_used': 'Du har redan haft din provperiod.',
  'plan.reason.trial_off': 'Provperioder är inte tillgängliga just nu.',
  'plan.source.grant': 'tilldelad dig',
  'plan.source.code': 'från en kod',
  'plan.source.trial': 'din provperiod',
  'plan.source.subscription': 'en prenumeration',

  // Account: plan and usage card.
  'plan.title': 'Plan och användning',
  'plan.classesLine': { one: '{held} av {count} pass i {month}', other: '{held} av {count} pass i {month}' },
  'plan.redeemed': 'Koden är inlöst.',
  'plan.trialStarted': 'Din provperiod har startat.',
  'plan.loadFailed': 'Din plan kunde inte laddas.',
  'plan.line': 'Din plan: {name}',
  'plan.lineSource': 'Din plan: {name} ({source})',
  'plan.lineEnd': 'Din plan: {name}, till {end}',
  'plan.lineSourceEnd': 'Din plan: {name} ({source}), till {end}',
  'plan.budgetLabel': 'Andel av månadens budget som har använts',
  'plan.budgetText': '{percent} % av månadens budget har använts. Den börjar om {day}.',
  'plan.firstFallback': 'den 1:a',
  'plan.redeemLabel': 'Lös in en kod',
  'plan.redeem': 'Lös in',
  'plan.trial': 'Starta din provperiod på {days} dagar',

  // Account: usage card.
  'usage.title': 'Användning',
  'usage.note.estimate_at_list_prices':
    'Kostnaden är en uppskattning till leverantörens listpriser. Det är inte en faktura.',
  'usage.note.sdk_retries':
    'Programvaran som pratar med leverantören försöker om tyst inom en förfrågan. De extra försöken räknas inte.',
  'usage.note.lost_lease_turn':
    'Ett steg som blev klart efter att classbeat redan hade lämnat över sitt arbete till en annan arbetsprocess kan inte registreras.',
  'usage.note.failed_stream_turn':
    'En förfrågan som misslyckades efter att leverantören börjat debitera den returnerar ingen användning och räknas därför inte.',
  'usage.note.other':
    'Det finns en annan orsak till att den här siffran kan skilja sig från leverantörens faktura.',
  'usage.kind.design_draft': 'Utformar ett pass',
  'usage.kind.revise_draft': 'Ändrar ett pass',
  'usage.loadFailed': 'Din användning kunde inte laddas.',
  'usage.period': 'Din användning i {month} (månader i UTC).',
  'usage.tokens': 'Tokens',
  'usage.cost': 'Beräknad kostnad',
  'usage.designed': 'Utformade pass',
  'usage.revisions': 'Revideringar',
  'usage.tokensExplained':
    'Tokens är de textbitar som utformaren läser och skriver: {input} in, {output} ut. Att spara till MOWL använder inga.',
  'usage.tokensExplainedReused':
    'Tokens är de textbitar som utformaren läser och skriver: {input} in, {output} ut och {reused} återanvända från tidigare förfrågningar. Att spara till MOWL använder inga.',
  'usage.byKind': 'Efter typ av arbete och modell',
  'usage.col.work': 'Arbete',
  'usage.col.model': 'Modell',
  'usage.col.tokens': 'Tokens',
  'usage.col.cost': 'Kostnad',
  'usage.leavesOut': 'Vad detta inte inkluderar',
  'usage.lowerThanBill': 'Siffran kan vara något lägre än leverantörens faktura.',

  // Account: MOWL connection card.
  'mowl.err.invalid_credentials': 'MOWL godkände inte den e-postadressen och det lösenordet.',
  'mowl.err.upstream_unavailable': 'Det gick inte att nå MOWL. Försök igen om några minuter.',
  'mowl.err.unknown': 'MOWL svarade på ett sätt som classbeat inte väntade sig. Försök igen senare.',
  'mowl.last.needs_reconnect': 'MOWL godkänner inte längre den sparade inloggningen. Anslut igen.',
  'mowl.last.decrypt_failed': 'Den sparade inloggningen kunde inte läsas. Anslut igen.',
  'mowl.last.rate_limited': 'MOWL bad classbeat att sakta ner.',
  'mowl.last.upstream_unavailable': 'Det gick inte att nå MOWL förra gången.',
  'mowl.last.other': 'Den senaste användningen av den här anslutningen misslyckades.',
  'mowl.connected': 'MOWL är anslutet.',
  'mowl.disconnected': 'MOWL är frånkopplat. classbeat använder inte längre dess inloggning.',
  'mowl.title': 'MOWL (Intelligent Cycling)',
  'mowl.loadFailed': 'Anslutningarna kunde inte laddas.',
  'mowl.status': 'Status',
  'mowl.statusConnected': 'Ansluten',
  'mowl.statusAgain': 'Behöver anslutas igen',
  'mowl.since': 'Ansluten sedan',
  'mowl.lastProblem': 'Senaste problem',
  'mowl.reconnectWarning': 'Om du ansluter igen ersätts den sparade inloggningen. {warning}',
  'mowl.signInText':
    'Logga in med ditt Intelligent Cycling-konto (MOWL). classbeat använder ditt lösenord en gång för att hämta en inloggningstoken och sparar det inte.',
  'mowl.emailLabel': 'MOWL-e-post',
  'mowl.passwordLabel': 'MOWL-lösenord',
  'mowl.connectButton': 'Anslut MOWL',
  'mowl.connectAgainButton': 'Anslut MOWL igen',
  'mowl.disconnectText':
    'Om du kopplar från raderas den token som classbeat har. MOWL ger inte classbeat något sätt att avsluta inloggningen hos sig; byt ditt MOWL-lösenord för att avsluta den där.',
  'mowl.confirmDisconnect': 'Bekräfta frånkoppling',
  'mowl.yesDisconnect': 'Ja, koppla från MOWL',
  'mowl.keep': 'Behåll anslutningen',
  'mowl.disconnect': 'Koppla från MOWL',

  // Account: Spotify working-playlist consent switch.
  'consent.revokeText':
    'Om du återkallar samtycket glömmer classbeat arbetsspellistan och slutar använda den. Spellistan finns kvar i ditt Spotify, privat, med sina senaste låtar; radera den där om du inte vill ha den. För att välja låtar krävs ditt samtycke igen, och nästa val skapar en ny arbetsspellista.',
  'consent.given': 'Tack. classbeat får använda arbetsspellistan.',
  'consent.withdrawn': 'Ditt samtycke är återkallat. Spellistan finns kvar i ditt Spotify.',
  'consent.label': 'Tillåt arbetsspellistan',
  'consent.stateOn': 'Du har tillåtit arbetsspellistan.',
  'consent.stateOff': 'Du har inte tillåtit den ännu.',
  'consent.confirmRevoke': 'Bekräfta återkallande',
  'consent.yesRevoke': 'Ja, återkalla mitt samtycke',
  'consent.keep': 'Behåll mitt samtycke',

  // Account: email address, password, delete, devices, email preferences, export.
  'cred.email.title': 'E-postadress',
  'cred.email.current': 'Din adress är {email}.',
  'cred.email.sent':
    'Om {email} kan användas är en bekräftelselänk på väg dit. Öppna den medan du är inloggad för att slutföra. Din adress ändras först då.',
  'cred.email.newLabel': 'Ny e-postadress',
  'cred.email.change': 'Ändra e-post',
  'cred.password.title': 'Lösenord',
  'cred.password.saved': 'Lösenordet sparades. Dina andra enheter loggades ut.',
  'cred.password.mismatch': 'De två lösenorden skiljer sig åt.',
  'cred.password.newLabel': 'Nytt lösenord',
  'cred.password.hint': '12 till 128 tecken.',
  'cred.password.repeatLabel': 'Upprepa det nya lösenordet',
  'cred.password.save': 'Spara lösenordet',
  'delete.button': 'Radera mitt konto',
  'delete.title': 'Radera ditt konto?',
  'delete.text':
    'Det här raderar ditt konto, dina pass och låtlistor, din plan och användning, dina anslutningar och din inloggningshistorik. Jobb som körs stoppas först. Det kan inte ångras; ladda ner dina data först om du vill ha en kopia. Träningspass som sparats i MOWL och spellistor i ditt Spotify finns kvar där. Skriv din e-postadress för att bekräfta.',
  'delete.emailLabel': 'Din e-postadress',
  'delete.confirm': 'Radera för gott',
  'delete.keep': 'Behåll mitt konto',
  'devices.title': 'Inloggade enheter',
  'devices.loadFailed': 'Enheterna kunde inte laddas. Ladda om sidan och försök igen.',
  'devices.unknownBrowser': 'Okänd webbläsare',
  'devices.lastUsed': 'Senast använd {when}',
  'devices.movedCurrent':
    'Använd från ett nytt nätverk eller en ny webbläsare {when}. Om det inte var du kan någon ha en kopia av den här sessionen: använd Logga ut överallt nedan och byt lösenord.',
  'devices.movedOther':
    'Använd från ett nytt nätverk eller en ny webbläsare {when}. Om det inte var du, logga ut den här enheten och byt lösenord.',
  'devices.newLocation': 'Ny plats',
  'devices.thisDevice': 'Den här enheten',
  'devices.signOutThis': 'Logga ut den här enheten',
  'devices.signOutAll': 'Logga ut överallt',
  'mailprefs.kind.welcome': 'Välkomstmeddelande',
  'mailprefs.kind.account_activity': 'Aviseringar om kontoaktivitet',
  'mailprefs.kind.class_ready': 'Ett pass är klart',
  'mailprefs.intro':
    'Välj vilka valfria e-postmeddelanden classbeat skickar. Länkar för inloggning, återställning av lösenord och bekräftelse skickas alltid. Aviseringar om kontoaktivitet omfattar säkerhetsaviseringar, till exempel misslyckade tvåfaktorsförsök och en ny inloggningsmetod; om du stänger av dem varnar classbeat dig inte om sådant via e-post.',
  'mailprefs.all': 'Valfri e-post',
  'export.text':
    'En fil med ditt konto, dina pass och versioner, låtlistor, jobb, användning och inloggningshistorik. Dina anslutningar visar bara sin status: aldrig en token, ett lösenord eller en nyckel.',
  'export.done': 'Dina data sparades i en fil.',
  'export.button': 'Ladda ner mina data',

  // Account: scan-with-MOWL panel, sign-in methods, two-factor.
  'mowlqr.err.upstream_unavailable':
    'Det gick inte att nå MOWL. Försök igen om några minuter, eller använd ditt lösenord.',
  'mowlqr.err.rate_limited': 'För många koder på kort tid. Vänta några minuter, eller använd ditt lösenord.',
  'mowlqr.err.unknown':
    'MOWL svarade på ett sätt som classbeat inte väntade sig. Försök igen senare, eller använd ditt lösenord.',
  'mowlqr.err.other': 'Koden fungerade inte. Försök igen, eller använd ditt lösenord.',
  'mowlqr.expired': 'Koden gick ut. Visa en ny för att försöka igen.',
  'mowlqr.cancelled': 'Koden stoppades.',
  'mowlqr.gone': 'Koden är inte längre giltig. Visa en ny.',
  'mowlqr.title': 'Skanna med MOWL-appen',
  'mowlqr.how':
    'Öppna MOWL-appen i telefonen, välj att skanna en kod och rikta kameran mot den här. classbeat ser aldrig ditt MOWL-lösenord.',
  'mowlqr.alt': 'QR-kod att skanna med MOWL-appen',
  'mowlqr.countdown': 'Koden går ut om {time}.',
  'mowlqr.stop': 'Stoppa',
  'mowlqr.intro': 'Inloggad i MOWL-appen i telefonen? Skanna en kod i stället för att skriva ditt lösenord.',
  'mowlqr.showNew': 'Visa en ny kod',
  'mowlqr.show': 'Visa koden',
  'signin.title': 'Inloggningsmetoder',
  'signin.password': 'Lösenord',
  'signin.passwordSet': 'Angett',
  'signin.passwordNotSet': 'Inte angett',
  'signin.unlink': 'Koppla bort {provider}',
  'signin.link': 'Koppla {provider}',
  'mfa.recoveryTitle': 'Spara de här återställningskoderna nu.',
  'mfa.recoveryText': '{title} Var och en fungerar en gång i stället för en appkod. De visas inte igen.',
  'mfa.recoveryList': 'Återställningskoder',
  'mfa.saved': 'Jag har sparat dem',
  'mfa.setUp': 'Konfigurera en autentiseringsapp',
  'mfa.scan': 'Skanna den här koden med en autentiseringsapp, eller skriv nyckeln för hand.',
  'mfa.qrLabel': 'QR-kod för din autentiseringsapp',
  'mfa.key': 'Nyckel: {key}',
  'mfa.codeLabel': 'Kod från appen',
  'mfa.turnOn': 'Slå på tvåfaktorsautentisering',
  'mfa.verifyLabel': 'Autentiserings- eller återställningskod',
  'mfa.verify': 'Verifiera',
  'mfa.title': 'Tvåfaktorsautentisering',
  'mfa.enabled': {
    one: 'En autentiseringsapp är konfigurerad. {count} återställningskod finns kvar.',
    other: 'En autentiseringsapp är konfigurerad. {count} återställningskoder finns kvar.',
  },
  'mfa.newCodes': 'Nya återställningskoder',
  'mfa.confirmRemove': 'Bekräfta borttagning av tvåfaktorsautentisering',
  'mfa.removeWarning':
    'Utan tvåfaktorsautentisering räcker ett stulet lösenord för att ändra det här kontot. Dina återställningskoder slutar också fungera.',
  'mfa.yesRemove': 'Ja, ta bort tvåfaktorsautentisering',
  'mfa.keep': 'Behåll tvåfaktorsautentisering',
  'mfa.remove': 'Ta bort tvåfaktorsautentisering',
  'mfa.offText':
    'Lägger till en kod från din telefon vid känsliga ändringar på den här sidan. Administratörer måste konfigurera det.',

  // Account: Spotify card and wizard, Swedebeat card.
  'spot.invalidClientId':
    'Det där ser inte ut som ett Spotify Client ID. Det består av 32 bokstäver och siffror (0-9 och a-f) och visas på appens inställningssida i Spotify Developer Dashboard.',
  'spot.featureOff': 'Låtval är inte tillgängligt just nu.',
  'spot.disconnectText':
    'Om du kopplar från raderas det classbeat har för Spotify: den sparade inloggningen, inställningen för arbetsspellistan och ditt samtycke till den, alla spellistssteg som lämnats oavgjorda, och länken mellan varje pass och den spellista som classbeat gjorde för det (passen själva finns kvar). Spellistor som redan finns i ditt Spotify finns kvar där, och classbeat rör dem inte igen. Spotify ger inte classbeat något sätt att avsluta anslutningen hos sig: ta bort classbeats åtkomst under Appar i dina Spotify-kontoinställningar.',
  'spot.changeText':
    'classbeat fortsätter använda den nuvarande anslutningen tills det nya Client ID har fungerat. Därefter ersätter det den sparade inloggningen och glömmer inställningen för arbetsspellistan, så du tillfrågas om ditt samtycke igen; spellistan finns kvar i ditt Spotify. Dina pass behåller sina spellistor. Om den nya appen loggar in ett annat Spotify-konto måste du bekräfta bytet, och classbeat raderar då det som Koppla från raderar.',
  'spot.title': 'Spotify',
  'spot.notConnected': 'Inte ansluten',
  'spot.setUp': 'Konfigurera Spotify',
  'spot.manage': 'Hantera Spotify',
  'spot.loadFailed': 'Spotify-anslutningen kunde inte laddas.',
  'spot.loading': 'Laddar',
  'spot.connected': 'Spotify är anslutet.',
  'spot.connectionTitle': 'Spotify-anslutning',
  'spot.intro':
    'classbeat ansluter till en Spotify-app som du äger själv, så begränsningarna är dina. Följ de här stegen en gång.',
  'spot.step1':
    'Öppna Spotify Developer Dashboard (developer.spotify.com/dashboard) och skapa en app. Den som äger appen behöver Spotify Premium. En app i Development Mode har en tillåtelselista på 5 personer: lägg till ditt eget Spotify-konto (namn och e-post) under User Management. Spotifys begränsningar för endpoints och anrop gäller.',
  'spot.step2': 'Kopiera classbeats callback-URL:',
  'spot.copyUrl': 'Kopiera callback-URL',
  'spot.copied': ' Kopierad.',
  'spot.copyFailed': ' Det gick inte att kopiera. Markera adressen och kopiera den.',
  'spot.step3': 'Klistra in den under appens Redirect URIs och spara.',
  'spot.step4':
    'Kopiera appens Client ID till fältet nedan. classbeat behöver aldrig appens Client Secret och ber inte om den.',
  'spot.step5': 'Tryck på knappen för att logga in hos Spotify och ge classbeat tillåtelse.',
  'spot.confirmHint': 'Ange Client ID igen och bekräfta för att byta till det andra Spotify-kontot.',
  'spot.clientId': 'Spotify Client ID',
  'spot.confirmSwitch': 'Bekräfta och byt konto',
  'spot.connectAgain': 'Anslut Spotify igen',
  'spot.connect': 'Anslut Spotify',
  'spot.keepId': 'Behåll nuvarande Client ID',
  'spot.changeId': 'Byt Client ID',
  'spot.changeConfirm': 'Bekräfta byte av Client ID',
  'spot.continue': 'Fortsätt',
  'spot.disconnectConfirm': 'Bekräfta frånkoppling',
  'spot.disconnected': 'Spotify är frånkopplat. classbeat använder inte längre dess inloggning.',
  'spot.disconnectYes': 'Ja, koppla från Spotify',
  'spot.disconnect': 'Koppla från Spotify',
  'spot.workingTitle': 'Arbetsspellista',
  'swed.err.invalid_credentials': 'Swedebeat godkände inte den e-postadressen och det lösenordet.',
  'swed.err.upstream_unavailable': 'Det gick inte att nå Swedebeat. Försök igen om några minuter.',
  'swed.err.app_key_unavailable': 'Inloggning hos Swedebeat är inte tillgänglig på den här servern.',
  'swed.err.feature_off': 'Swedebeat är inte påslaget just nu.',
  'swed.err.unknown': 'Swedebeat svarade på ett sätt som classbeat inte väntade sig. Försök igen senare.',
  'swed.last.needs_reconnect': 'Swedebeat godkänner inte längre den sparade inloggningen. Anslut igen.',
  'swed.last.decrypt_failed': 'Den sparade inloggningen kunde inte läsas. Anslut igen.',
  'swed.last.rate_limited': 'Swedebeat bad classbeat att sakta ner.',
  'swed.last.upstream_unavailable': 'Det gick inte att nå Swedebeat förra gången.',
  'swed.last.other': 'Den senaste användningen av den här anslutningen misslyckades.',
  'swed.deviceInUse':
    'Ditt Swedebeat-konto är inloggat på en annan enhet, troligen Swedebeat-appen i telefonen. Om du loggar in här ändå loggas den enheten ut från Swedebeat.',
  'swed.title': 'Swedebeat',
  'swed.connected': 'Swedebeat är anslutet.',
  'swed.disconnected': 'Swedebeat är frånkopplat. classbeat har inte längre dess inloggning.',
  'swed.loadFailed': 'Anslutningarna kunde inte laddas.',
  'swed.use': 'Jag använder Swedebeat',
  'swed.useOn': 'classbeat erbjuder dina Swedebeat-mixar när du utformar ett pass.',
  'swed.useOff':
    'Slå på det här om du undervisar med Swedebeat-musik. Tills dess visar classbeat inget om Swedebeat.',
  'swed.notAvailable': 'Inloggning hos Swedebeat är inte tillgänglig på den här servern.',
  'swed.account': 'Swedebeat-konto',
  'swed.intro':
    'Swedebeat är valfritt. Logga in med ditt Swedebeat-konto. classbeat använder ditt lösenord en gång för att hämta en inloggningstoken och sparar det inte.',
  'swed.email': 'Swedebeat-e-post',
  'swed.password': 'Swedebeat-lösenord',
  'swed.again': 'Ange ditt lösenord igen för att logga in här ändå.',
  'swed.anyway': 'Logga in här ändå',
  'swed.connectAgain': 'Anslut Swedebeat igen',
  'swed.connect': 'Anslut Swedebeat',
  'swed.keepOther': 'Behåll den andra enheten inloggad',
  'swed.disconnectNote':
    'Om du kopplar från raderas den inloggning som classbeat har. Du loggas inte ut ur Swedebeat-appen.',
  'swed.confirmLabel': 'Bekräfta frånkoppling av Swedebeat',
  'swed.confirmText': 'classbeat glömmer din Swedebeat-inloggning. Du kan ansluta igen när som helst.',
  'swed.disconnectYes': 'Ja, koppla från Swedebeat',
  'swed.disconnect': 'Koppla från Swedebeat',

  // Swedebeat match and hand-off on the class page, the reconnect prompt, the account page.
  'swmatch.found': 'Hittad i Swedebeat',
  'swmatch.unsure': 'Osäker matchning',
  'swmatch.missingChip': 'Finns inte i Swedebeat',
  'swmatch.notConnected':
    'Spotify, MOWL eller Swedebeat är inte anslutet. Anslut det först på din kontosida.',
  'swmatch.title': 'Swedebeat-matchning',
  'swmatch.totals': '{found} hittade, {unsure} osäkra, {missing} finns inte i Swedebeat.',
  'swmatch.length': 'Mixen skulle bli {length} lång med Swedebeats versioner.',
  'swmatch.lengthPlanned':
    'Mixen skulle bli {length} lång med Swedebeats versioner, mot {planned} planerade.',
  'swmatch.songs': 'Matchade låtar',
  'swmatch.has': 'Swedebeat har: {title}, {artist}',
  'swmatch.hasDuration': 'Swedebeat har: {title}, {artist} ({duration})',
  'swmatch.missing': {
    one: 'En låt finns inte i Swedebeat. Om du behåller den saknas den i mixen.',
    other: '{count} låtar finns inte i Swedebeat. Om du behåller dem saknas de i mixen.',
  },
  'swmatch.checkUnsure':
    'Kontrollera de osäkra låtarna ovan. Behåll dem bara om Swedebeats version är samma låt.',
  'swmatch.waitJob': 'Vänta först tills den pågående uppgiften är klar.',
  'swmatch.approved': 'Urvalet är godkänt',
  'swmatch.nothingWritten': 'Inget skrevs till Swedebeat.',
  'swmatch.approve': 'Godkänn det här urvalet',
  'swmatch.replace': 'Byt ut de saknade och osäkra låtarna',
  'swstart.blurb':
    'Se vilka av de här låtarna som finns i Swedebeat, så att du kan bygga mixen där. Bara titlar och artister slås upp, och inget skrivs till ditt Swedebeat-konto.',
  'swstart.connectFirst': 'Anslut Swedebeat på din kontosida först',
  'swstart.match': 'Matcha med Swedebeat',
  'swstart.loadFailed': 'Swedebeat-matchningen kunde inte laddas. Ladda om sidan.',
  'swstart.loading': 'Laddar Swedebeat-matchningen',
  'swreconnect.text':
    'Swedebeat eller MOWL godkänner inte längre den sparade inloggningen, så inget skickades. Anslut igen och kom sedan tillbaka till det här passet.',
  'swreconnect.link': 'Anslut igen på din kontosida',
  'swnext.title': 'Nästa steg, i Swedebeat-appen',
  'swnext.ok': 'Det är din mix. Dess längd stämmer med ditt urval.',
  'swnext.tooShort': 'Mixen är kortare än väntat.',
  'swnext.tooShortWhere': 'Mixen är kortare än väntat. Låtarna {positions} kan saknas.',
  'swnext.tooLong': 'Mixen är längre än väntat. En låt kan ha lagts till eller upprepats.',
  'swnext.notFound':
    'Swedebeat har inte visat någon ny mix ännu. Gör klart den i appen, låt den synkas och kontrollera sedan igen.',
  'swnext.stopped': 'Kontrollen stoppades. Kontrollera igen.',
  'swnext.notNow': 'Inte nu. Ditt urval förblir godkänt; kom tillbaka till det här passet när du är redo.',
  'swnext.showAgain': 'Visa stegen igen',
  'swnext.playlist': 'Spellista: {name}.',
  'swnext.facts': '{count} låtar, ungefär {length} långt som mix.',
  'swnext.step1': 'Öppna Swedebeat-appen.',
  'swnext.step2Named': 'Matcha spellistan ”{name}”.',
  'swnext.step2': 'Matcha din spellista.',
  'swnext.step3': 'Skapa mixen.',
  'swnext.step4': 'Låt den synkas och kom sedan tillbaka hit.',
  'swnext.expiry':
    'Du kan lämna sidan och komma tillbaka. Det här steget förblir öppet till {when}; därefter startar du det igen.',
  'swnext.expired': 'Det här steget har gått ut. Tryck på knappen för att starta det igen.',
  'swnext.checking': 'Kontrollerar dina mixar i MOWL',
  'swnext.prefOff':
    '”Jag använder Swedebeat” är avstängt. Slå på det på {link} för att starta det här steget eller utforma passet på mixen. Ett steg som du redan har startat kan fortfarande kontrolleras.',
  'swnext.accountPage': 'din kontosida',
  'swnext.prefUnknown':
    'classbeat kunde inte kontrollera din Swedebeat-inställning. Ladda om sidan för att utforma passet på den här mixen.',
  'swnext.verified': 'Mixen är verifierad',
  'swnext.design': 'Utforma passet på den här mixen',
  'swnext.checkAgain': 'Kontrollera igen',
  'swnext.made': 'Jag gjorde mixen',
  'swnext.later': 'Inte nu',
  'swnext.right': 'Det här är rätt mix',
  'swnext.mixesFailed': 'Dina Swedebeat-mixar kunde inte laddas från MOWL. Kontrollera igen senare.',
  'swnext.mixesLoading': 'Laddar dina mixar',
  'swnext.which': 'Vilken gjorde du?',
  'swnext.new': 'Ny',
  'account.oauth.linked': 'Inloggningsmetoden är kopplad till ditt konto.',
  'account.oauth.reauthed': 'Tack, du är bekräftad. Upprepa ändringen du höll på med.',
  'account.tab.connections': 'Anslutningar',
  'account.tab.plan': 'Plan och användning',
  'account.tab.security': 'Inloggning och säkerhet',
  'account.tab.data': 'Dina data',
  'account.tab.appearance': 'Utseende',
  'account.heading': 'Ditt konto',
  'account.tabs': 'Konto',
  'account.usageDetails': 'Visa användningsdetaljer',
  'account.email': 'E-post',
  'account.signOut': 'Logga ut',
  'account.language': 'Språk',
  'account.theme': 'Tema',
  'account.themeHelp': 'Mörkt är standard. Samma som enheten följer din telefon eller dator.',
}

export default sv
