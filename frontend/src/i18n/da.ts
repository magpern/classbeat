import type { Catalogue } from './en'
import { helpDa } from './help.da'
import { legalDa } from './legal.da'

const da: Catalogue = {
  ...legalDa,
  ...helpDa,
  'language.label': 'Sprog',
  'language.follow': 'Følg denne browser',
  'language.help':
    'Sproget på dette websted og i dine mails. »Følg denne browser« bruger browserens sprog på hver enhed, og dine mails er så på engelsk.',
  'format.soonUnknown': 'om nogle få minutter',
  'usage.underOneCent': 'under {amount}',

  // Words the screens of this area share.
  'common.loading': 'Indlæser',
  'common.cancel': 'Annuller',
  'common.continue': 'Fortsæt',
  'common.email': 'Mail',
  'common.password': 'Adgangskode',

  // Landing page.
  'landing.badge': 'Privat beta',
  'landing.title':
    'Beskriv dit indendørscykelhold, så vælger classbeat sangene og afstemmer indsatserne efter musikken.',
  'landing.signUp': 'Opret dig med en invitationskode',
  'landing.haveAccount': 'Jeg har allerede en konto',
  'landing.example.title': 'Et eksempelhold',
  'landing.example.note': 'En opdigtet tur på 30 minutter. Sangene og kunstnerne nedenfor er pladsholdere.',
  'landing.example.songs': 'Sangene på eksempelholdet',
  'landing.example.songMeta': '{artist} · {part}',
  'landing.example.songStats': '{bpm} BPM · {length}',
  'landing.example.part.warmUp': 'Opvarmning',
  'landing.example.part.firstClimb': 'Første stigning',
  'landing.example.part.sprints': 'Sprints',
  'landing.example.part.secondClimb': 'Anden stigning',
  'landing.example.part.coolDown': 'Nedkøling',
  'landing.how.title': 'Sådan virker det',
  'landing.how.describe.title': 'Beskriv',
  'landing.how.describe.text':
    'Fortæl classbeat, hvor langt holdet skal være, hvor hårdt det skal være, og hvilken playliste du vil bruge.',
  'landing.how.approve.title': 'Godkend sange',
  'landing.how.approve.text': 'Se de sange igennem, classbeat har valgt, og skift dem ud, der ikke passer.',
  'landing.how.save.title': 'Gem i MOWL',
  'landing.how.save.text': 'Dit færdige hold sendes direkte til din MOWL-konto, klar til at cykle.',

  // Sign in, sign up and the pages behind the mail links.
  'auth.signIn': 'Log ind',
  'auth.createAccount': 'Opret en konto',
  'auth.createAccountSubmit': 'Opret konto',
  'auth.newHere': 'Ny her? Opret en konto',
  'auth.haveAccountSignIn': 'Har du allerede en konto? Log ind',
  'auth.inviteLead': 'classbeat er foreløbig kun for inviterede. Skriv den invitationskode, du har fået.',
  'auth.inviteFirst': 'Skriv først din invitationskode.',
  'auth.inviteCode': 'Invitationskode',
  'auth.continueWith': 'Fortsæt med {provider}',
  'auth.or': 'ELLER',
  'auth.passwordHint': '12 til 128 tegn. Du skriver den igen, når du bekræfter.',
  'auth.legal':
    'Ved at fortsætte accepterer du at deltage i betaen, som kun er for inviterede. classbeat er ikke lavet af eller tilknyttet MOWL, Spotify eller Swedebeat.',
  'auth.forgot': 'Har du glemt din adgangskode?',
  'auth.checkInbox': 'Tjek din indbakke',
  'auth.backToSignIn': 'Tilbage til log ind',
  'signup.sent':
    'Hvis denne adresse kan bruges, har vi sendt et link til den. Åbn linket, og skriv din adgangskode igen for at gøre det færdigt. Linket virker i 24 timer.',
  'confirm.title': 'Bekræft din konto',
  'confirm.noLink':
    'Denne side kræver linket fra bekræftelsesmailen. Åbn linket igen, eller opret dig igen for at få et nyt.',
  'confirm.doneTitle': 'Kontoen er oprettet',
  'confirm.done': 'Din konto er klar. Log ind for at fortsætte.',
  'confirm.intro':
    'Skriv den adgangskode, du valgte, da du oprettede dig. Hvis du oprettede dig med en udbyder som Microsoft, skal du lade feltet stå tomt.',
  'confirm.submit': 'Bekræft konto',
  'emailChange.title': 'Bekræft din nye e-mailadresse',
  'emailChange.noLink': 'Denne side kræver linket fra bekræftelsesmailen.',
  'emailChange.changedTitle': 'E-mailadressen er ændret',
  'emailChange.changed': 'Din konto bruger nu {email}. Andre enheder er blevet logget ud.',
  'emailChange.back': 'Tilbage til din konto',
  'emailChange.signInFirst':
    'Log ind på den konto, der bad om ændringen, og åbn derefter linket i mailen igen.',
  'emailChange.confirmIntro':
    'Du er logget ind som {email}. Bekræft, at denne konto skal bruge den nye adresse.',
  'emailChange.confirm': 'Bekræft ny e-mailadresse',
  'providerEmail.loadingTitle': 'Næsten færdig',
  'providerEmail.sent':
    'Hvis vi kan bruge {email}, er et bekræftelseslink på vej. Åbn det i samme browser for at gøre det færdigt. Linket virker i 24 timer.',
  'providerEmail.mistyped':
    'Skrev du forkert adresse? Der kan ikke sendes mere fra denne side, fordi et andet forsøg ikke ville sende noget. Start login hos udbyderen igen for at få en ny chance.',
  'providerEmail.again': 'Start login hos udbyderen igen',
  'providerEmail.nothingTitle': 'Intet at gøre færdigt her',
  'providerEmail.nothing':
    'Intet login hos en udbyder venter på en adresse i denne browser. Det kan være udløbet (efter 15 minutter) eller allerede være gjort færdigt.',
  'providerEmail.yourProvider': 'din udbyder',
  'providerEmail.title': 'Bekræft din e-mailadresse',
  'providerEmail.intro':
    '{provider} bekræftede ikke en e-mailadresse for dig. Skriv den adresse, du vil bruge til classbeat; vi sender dig et link til at bekræfte den.',
  'providerEmail.submit': 'Send bekræftelseslink',
  'reset.sent':
    'Hvis en konto bruger den adresse, er et link til at vælge en ny adgangskode på vej. Det virker i 30 minutter.',
  'reset.title': 'Nulstil din adgangskode',
  'reset.intro': 'Skriv din e-mailadresse, så sender vi dig et link.',
  'reset.send': 'Send nulstillingslink',
  'reset.changedTitle': 'Adgangskoden er ændret',
  'reset.changed': 'Din adgangskode er ændret, og du er logget ud overalt. Log ind med den nye.',
  'reset.chooseTitle': 'Vælg en ny adgangskode',
  'reset.checking': 'Kontrollerer dit link',
  'reset.newLink': 'Få et nyt link',
  'reset.newPassword': 'Ny adgangskode',
  'reset.passwordHint': '12 til 128 tegn.',
  'reset.linked': 'Tilknyttede loginmetoder',
  'reset.linkedHint':
    'De kan også bruges til at logge ind på din konto. Fjern dem, du ikke kender eller ikke bruger mere.',
  'reset.remove': 'Fjern {provider}',
  'reset.submit': 'Skift adgangskode',

  // The confirm-it-is-you prompts of the guard.
  'guard.reauthTitle': 'Bekræft, at det er dig',
  'guard.reauthIntro': 'Denne ændring kræver et nyligt login. Bekræft din identitet for at fortsætte.',
  'guard.code': 'Kode fra godkendelsesappen eller gendannelseskode',
  'guard.confirmPassword': 'Bekræft med adgangskode',
  'guard.confirmCode': 'Bekræft med kode',
  'guard.confirmProvider': 'Bekræft med {provider}',
  'guard.oidc':
    '{providers} kan kun bekræfte det, hvis du har logget ind dér inden for de seneste 5 minutter.',
  'guard.oidcPassword':
    '{providers} kan kun bekræfte det, hvis du har logget ind dér inden for de seneste 5 minutter. Din adgangskode virker altid.',
  'guard.stepUpTitle': 'Skriv en tofaktorkode',
  'guard.stepUpIntro':
    'Denne ændring kræver en kode fra din godkendelsesapp eller en af dine gendannelseskoder.',

  // The frame around every page.
  'shell.nav.main': 'Hovedmenu',
  'shell.nav.home': 'Forside',
  'shell.nav.classes': 'Mine hold',
  'shell.nav.new': 'Nyt hold',
  'shell.nav.music': 'Musik',
  'shell.nav.account': 'Konto',
  'shell.brandHome': 'classbeat forside',
  'shell.signUp': 'Opret dig',
  'shell.backHome': 'Tilbage til forsiden',
  'shell.close': 'Luk',
  'cookies.label': 'Information om cookies',
  'cookies.text':
    'classbeat bruger kun de cookies, der skal til for at logge dig ind og huske dine indstillinger, for eksempel sprog og tema, og loginsiderne bruger et sikkerhedstjek fra Cloudflare. Vi sporer dig ikke.',
  'cookies.ok': 'OK',
  'shell.menu.signOut': 'Log ud',
  'shell.classesThisMonth': 'Hold denne måned',
  'shell.classesThisMonthLine': 'Hold denne måned: {line}',
  'shell.api.checking': 'API: kontrollerer…',
  'shell.api.ok': 'API: ok',
  'shell.api.unreachable': 'API: kan ikke nås',
  'shell.admin': 'Admin',
  'shell.notAdmin': 'Din konto er ikke en adminkonto.',
  'shell.notFound': 'Siden findes ikke',
  'shell.backToStart': 'Tilbage til start',
  'shell.requireAuth.loadingTitle': 'Et øjeblik',
  'shell.requireAuth.errorTitle': 'Serveren kan ikke nås',
  'shell.requireAuth.errorTryAgain': 'Prøv igen om lidt.',
  'shell.requireAuth.title': 'Log ind først',
  'shell.requireAuth.text': 'Denne side er for brugere, der er logget ind.',

  // Plain-language texts for the reason codes of the auth API (api/messages.ts).
  'error.reason.confirmation_mismatch':
    'Det er ikke e-mailadressen til denne konto. Intet er blevet slettet.',
  'error.reason.invalid_invite': 'Den invitationskode er ikke gyldig, eller den er allerede brugt.',
  'error.reason.invite_invalid': 'Invitationen til denne oprettelse er ikke gyldig længere. Bed om en ny.',
  'error.reason.signups_closed': 'Oprettelse af konti er lukket lige nu.',
  'error.reason.password_rule': 'Vælg en adgangskode på 12 til 128 tegn.',
  'error.reason.invalid_credentials': 'E-mailadressen eller adgangskoden er forkert.',
  'error.reason.wrong_password': 'Det er ikke den adgangskode, du valgte, da du oprettede dig.',
  'error.reason.invalid_token':
    'Dette link er ikke gyldigt længere. Det kan være udløbet eller allerede brugt.',
  'error.reason.wrong_browser':
    'Gør det færdigt på den enhed og i den browser, hvor du startede login. Dette link virker ikke her.',
  'error.reason.account_exists': 'Der findes allerede en konto med denne adresse. Log ind på den i stedet.',
  'error.reason.email_taken': 'Den adresse bruges allerede af en anden konto.',
  'error.reason.last_sign_in_method': 'Det er din eneste måde at logge ind på, så den kan ikke fjernes.',
  'error.reason.invalid_code': 'Koden er forkert. Tjek appen, og prøv igen.',
  'error.reason.totp_changed': 'Din tofaktoropsætning blev ændret i mellemtiden. Start forfra.',
  'error.reason.totp_not_enabled': 'Tofaktorgodkendelse er ikke sat op.',
  'error.reason.not_authenticated': 'Du er logget ud. Log ind, og prøv igen.',
  'error.reason.no_pending_signup': 'Intet login venter på en adresse. Start login hos udbyderen igen.',
  'error.reason.provider_disabled': 'Den loginmetode er ikke tilgængelig.',
  'error.reason.reauth_use_password': 'Din konto har en adgangskode, så bekræft med din adgangskode.',
  'error.reason.forbidden': 'Din konto må ikke gøre det.',
  'error.reason.busy': 'Serveren er optaget. Prøv igen om nogle sekunder.',
  'error.reason.captcha_failed': 'Sikkerhedstjekket blev ikke godkendt. Prøv igen.',
  'error.reason.captcha_unavailable': 'Sikkerhedstjekket kan ikke nås lige nu. Prøv igen om et minut.',
  // ADR 0025 section 11: the Turnstile check on the sign-in, sign-up, provider-email and reset forms.
  'captcha.label': 'Sikkerhedstjek',
  'captcha.waiting': 'Tjekker, at du er et menneske…',
  'captcha.session': 'Siden kunne ikke afgøre, om et sikkerhedstjek er nødvendigt. Genindlæs siden, og prøv igen.',
  'captcha.expired': 'Sikkerhedstjekket udløb. Det kører igen.',
  'captcha.error': 'Sikkerhedstjekket løb ind i et problem og prøver igen. Genindlæs siden, hvis det bliver ved.',
  'captcha.unsupported': 'Denne browser kan ikke køre sikkerhedstjekket. Prøv en anden browser.',
  'captcha.unloaded': 'Sikkerhedstjekket kunne ikke indlæses. Tillad challenges.cloudflare.com i din browser og eventuelle blokeringer, eller prøv en anden browser.',
  'captcha.fake': 'Jeg er et menneske (testtjek)',
  'error.tooMany': 'For mange forsøg. Vent lidt, og prøv igen.',
  'error.tooManyWait': 'For mange forsøg. Prøv igen om {wait}.',
  // Both forms read the same in English today; the other languages need the split.
  'error.waitMinutes': { one: '{count} minut', other: '{count} minutter' },
  'error.waitSeconds': { one: '{count} sekund', other: '{count} sekunder' },
  'error.fields': 'Tjek felterne, og prøv igen.',
  'error.server': 'Noget gik galt på serveren. Prøv igen om lidt.',
  'error.network': 'Serveren kunne ikke nås. Tjek din forbindelse.',
  'error.generic': 'Det virkede ikke. Prøv igen.',

  // Outcome codes the OAuth callback puts in ?oauth= of /signin, /signup and /account.
  'oauth.invalid': 'Login kunne ikke gennemføres. Prøv igen.',
  'oauth.denied': 'Login blev annulleret hos udbyderen.',
  'oauth.session_mismatch': 'Du blev logget ud eller skiftede konto under login. Log ind igen.',
  'oauth.account_exists':
    'Der findes allerede en konto med den e-mailadresse. Log ind på den med din adgangskode, og tilknyt derefter udbyderen fra din kontoside.',
  'oauth.invite_required':
    'Du skal have en invitation for at oprette en konto. Skriv din invitationskode nedenfor, og prøv igen.',
  'oauth.invite_invalid': 'Den invitation er ikke gyldig længere. Bed om en ny.',
  'oauth.identity_taken': 'Den udbyderkonto er allerede tilknyttet en anden classbeat-konto.',
  'oauth.identity_mismatch': 'Det er ikke den udbyderkonto, der er tilknyttet din classbeat-konto.',
  'oauth.reauth_use_password':
    'Din konto har en adgangskode, så Google kan ikke bekræfte, at det er dig. Bekræft med din adgangskode. Intet er blevet ændret.',
  'oauth.reauth_expired': 'Bekræftelsen tog længere end 5 minutter, så intet er blevet ændret. Prøv igen.',
  'oauth.reauth_stale':
    'Udbyderen bekræftede ikke, at du har logget ind igen inden for de seneste 5 minutter, så intet er blevet ændret.',
  'oauth.reauth_stale_password':
    'Udbyderen bekræftede ikke, at du har logget ind igen inden for de seneste 5 minutter, så intet er blevet ændret. Brug i stedet din adgangskode.',
  'oauth.reauth_stale_set_password':
    'Udbyderen bekræftede ikke, at du har logget ind igen inden for de seneste 5 minutter, så intet er blevet ændret. Hvis du vil oprette en adgangskode, skal du logge ud og vælge »Har du glemt din adgangskode?« på loginsiden. Bekræft derefter med den.',
  'oauth.reauth_stale_both':
    'Udbyderen bekræftede ikke, at du har logget ind igen inden for de seneste 5 minutter, så intet er blevet ændret. Brug i stedet din adgangskode, hvis du har en. Hvis du vil oprette en adgangskode, skal du logge ud og vælge »Har du glemt din adgangskode?« på loginsiden. Bekræft derefter med den.',
  'oauth.no_account':
    'Ingen classbeat-konto er tilknyttet {account}. Opret en konto for at deltage i betaen, som kun er for inviterede.',
  'oauth.providerAccount': '{provider}-kontoen',
  'oauth.providerAccountOther': 'udbyderkontoen',
  // Server sentences by code (ADR 0023 section 6): the text equals the backend's English; the
  // page words a code from here and shows the server's text for a code it does not have.
  'sentence.spotify.invalid_grant':
    'Spotify accepterer ikke forbindelsen længere. Opret forbindelse til Spotify igen.',
  'sentence.spotify.app_misconfigured':
    'Spotify genkender ikke denne appopsætning. Tjek Client ID og omdirigerings-URI’en i din Spotify-app.',
  'sentence.spotify.not_allowlisted':
    'Spotify afviste denne konto. Tilføj kontoens navn og e-mailadresse under User Management i din Spotify-app (udviklertilstand tillader 5 personer), og sørg for, at appens ejer har Spotify Premium.',
  'sentence.spotify.forbidden': 'Spotify afviste anmodningen for din konto eller playliste.',
  'sentence.spotify.rate_limited': {
    one: 'Spotify beder os sætte tempoet ned; prøver igen om cirka {count} minut.',
    other: 'Spotify beder os sætte tempoet ned; prøver igen om cirka {count} minutter.',
  },
  'sentence.spotify.quota_exhausted': {
    one: 'Din Spotify-app har brugt sin kvote op for nu. Prøv igen om cirka {count} time.',
    other: 'Din Spotify-app har brugt sin kvote op for nu. Prøv igen om cirka {count} timer.',
  },
  'sentence.spotify.upstream_unavailable': 'Spotify svarer ikke.',
  'sentence.spotify.not_found': 'Spotify kunne ikke finde den playliste.',
  'sentence.spotify.scope_mismatch':
    'Spotify gav ikke den adgang, classbeat har brug for. Opret forbindelse igen, og tillad den.',
  'sentence.spotify.invalid_response':
    'Spotify gav ikke den adgang, classbeat har brug for. Opret forbindelse igen, og tillad den.',
  'sentence.spotify.account_changed':
    'Det er en anden Spotify-konto end før. Bekræft for at skifte (classbeat glemmer arbejdsplaylisten).',
  'sentence.spotify.access_denied': 'Spotify blev ikke tilsluttet.',
  'sentence.spotify.unknown': 'Noget gik galt med Spotify. Prøv igen senere.',
  'sentence.job.consent_missing': 'Tillad arbejdsplaylisten i dine Spotify-indstillinger først.',
  'sentence.job.pool_unusable':
    'Din arbejdsplayliste er blevet ændret: gør den privat igen, eller sæt den op igen.',
  'sentence.job.pool_missing': 'Din arbejdsplayliste var væk fra Spotify; prøver igen af sig selv.',
  'sentence.job.pool_not_synced':
    'MOWL er ikke nået til at følge med arbejdsplaylisten; prøv igen om et minut.',
  'sentence.job.playlist_mismatch':
    'Spotifys playliste passede ikke til de godkendte sange; intet blev designet. Opret den igen.',
  'sentence.job.pool_too_small': 'Der blev ikke fundet nok brugbare sange; prøv en bredere stil eller epoke.',
  'sentence.job.account_changed':
    'Din Spotify-konto er ikke den, som disse sange blev valgt til. Bed om nye sange.',
  'sentence.job.pool_busy': 'Et andet sangvalg kører stadig.',
  'sentence.job.media_host_not_allowed':
    'Mixet blev føjet til din MOWL-konto, men det kunne ikke analyseres: det er endnu ikke muligt at analysere et nyt Swedebeat-mix på denne server. Mix, der er analyseret før, virker stadig.',
  'sentence.job.media_refused': 'Lyden i dette mix kunne ikke hentes. Intet blev designet.',
  'sentence.job.analysis_failed': 'Lyden i dette mix kunne ikke analyseres. Prøv et andet mix.',
  'sentence.job.feature_off': 'Denne funktion er slået fra på denne server lige nu. Intet blev designet.',
  'sentence.job.analysis_limit':
    'Du har analyseret 10 nye Swedebeat-mix inden for de seneste 24 timer. Mix, der er analyseret før, virker stadig; prøv et nyt i morgen.',
  'sentence.job.handoff_expired':
    'Den kontrol er udløbet. Start forfra fra Swedebeat-trinnet; intet blev hentet.',
  'sentence.swedebeat.wait': {
    one: 'Swedebeat beder os sætte tempoet ned; prøver igen om cirka {count} minut.',
    other: 'Swedebeat beder os sætte tempoet ned; prøver igen om cirka {count} minutter.',
  },
  'sentence.swedebeat.stopped': 'Swedebeat beder os sætte tempoet ned. Dette match er stoppet.',

  // Class page, my classes, versions, save, undecided writes and pattern notes (ML5c). The class
  // text itself (names, cues, descriptions, song titles) is generated and stays as written.
  'class.tryAgain': 'Prøv igen',
  'class.checkNeeded': 'Tjek nødvendigt',
  'class.checkAgain': 'Tjek igen',
  'class.doItAnyway': 'Gør det alligevel',
  'class.confirm': 'Bekræft',
  'class.cancel': 'Annuller',
  'class.goToAccount': 'Gå til din konto',
  'class.waitRunning': 'Vent først, til den igangværende opgave er færdig.',
  'class.runDropped': 'Forbindelsen til serveren blev afbrudt. Prøver igen.',
  'class.runStopping': 'Stopper. Det tager et øjeblik.',
  'class.runWaiting': 'Venter på en ledig plads.',
  'class.versions': 'Versioner',
  'class.version': 'Version {number}',
  'class.useThisVersion': 'Brug denne version',
  'class.useVersionAria': 'Brug denne version {number}',
  'class.view': 'Vis',
  'class.viewing': 'Vises',
  'class.viewVersionAria': 'Vis version {number}',
  'class.viewingVersionAria': 'Viser version {number}',
  'class.fromVersion': 'fra version {number}',
  'class.current': 'Aktuel',
  'class.column.length': 'Længde',
  'class.column.zone': 'Zone',
  'class.column.incline': 'Hældning',
  'class.column.cadence': 'Kadence',
  'class.column.position': 'Position',
  'class.column.cue': 'Instruktion',
  'class.position.standing': 'Stående',
  'class.position.seated': 'Siddende',
  'class.position.standingLower': 'stående',
  'class.position.seatedLower': 'siddende',
  'class.kind.warmup': 'Opvarmning',
  'class.kind.main': 'Hoveddel',
  'class.kind.recovery': 'Restitution',
  'class.kind.cooldown': 'Nedkøling',
  'class.cadence.beat': 'i takt med musikken',
  'class.cadence.range': '{low} til {high} rpm',
  'class.zone.short': 'Z{zone}',
  'class.zone.line': 'Z{zone} {name}',
  'class.unknownBpm': 'MOWL kendte endnu ikke tempoet på disse sange, så kadencen på dem er et gæt:',

  // Reasons the class API refuses with (ADR 0007 sections 2, 3 and 12).
  'class.reason.stale_base':
    'Dette hold blev ændret i en anden fane eller et andet vindue, så din anmodning blev ikke sendt. Siden er indlæst igen. Læs den aktuelle version, og spørg igen.',
  'class.reason.hash_mismatch':
    'Den version er ikke den, som siden viste: den blev ændret et andet sted. Siden er indlæst igen.',
  'class.reason.class_busy':
    'Dette hold er optaget af en anden opgave, måske fra en anden fane. Siden er indlæst igen. Vent, til den er færdig, og prøv så igen.',
  'class.reason.revision_limit':
    'Dette hold har nået det højeste antal ændringer, et hold kan have. Behold den version, du kan lide, eller design et nyt hold.',
  'class.reason.not_changeable':
    'Dette hold har ingen egen musik, så det kan ikke ændres via chatten. Du kan stadig se, bruge og gemme dets versioner.',
  'class.reason.chat_full':
    'Denne samtale er fuld. Behold den version, du kan lide, eller design et nyt hold.',
  'class.reason.not_found': 'Dette hold, eller den version, findes ikke længere. Siden er indlæst igen.',
  'class.reason.not_connected': 'MOWL er ikke tilsluttet. Tilslut det på din kontoside, og spørg så igen.',
  'class.reason.invalid_input': 'Den anmodning kunne ikke bruges. Tjek den, og prøv igen.',
  'class.reason.call_may_be_running':
    'classbeat skriver måske stadig dette hold til MOWL, og hvis du sletter det nu, forsvinder den eneste registrering af den skrivning. Vent et par minutter, og prøv så at slette det igen.',
  'class.reason.music_unlinked':
    'Musikken i denne version findes ikke i din tilsluttede MOWL-konto. Ændr den, og gem den nye version.',
  'class.reason.running_not_evaluated':
    'Løbehold kan ikke ændres på din plan lige nu, så din anmodning blev ikke sendt, og der blev ikke trukket noget. Prøv igen senere.',
  'class.reason.no_beat_data':
    'Denne version har ingen beat-data at rette. Bed først om en ændring via chatten, og prøv så igen.',
  'class.reason.correction_limit':
    'Dette hold har så mange tidsrettelser, som det kan rumme. Nulstil nogle, og prøv så igen.',
  'class.reason.nothing_pending': 'Den aktuelle version bruger allerede disse tidsrettelser.',
  'class.reason.refit_limit':
    'Timingen på dette hold er rettet så mange gange, som det er muligt. Design et nyt hold for at starte forfra.',
  'class.reason.changedMeanwhile': 'Dette hold blev ændret i mellemtiden. Siden er indlæst igen.',
  'class.origin.design_draft': 'Første design',
  'class.origin.revise_draft': 'Ændret efter ønske',
  'class.origin.other': 'Version',
  'class.change.segments': 'Segmenter: {added} tilføjet, {removed} fjernet.',
  'class.change.intervals': 'Intervaller: {added} tilføjet, {removed} fjernet, {changed} ændret.',
  'class.change.lengthSame': 'Længden er uændret, {length}.',
  'class.change.length': 'Længde: {from} til {to}.',
  'class.change.tssSame': 'TSS er uændret, {tss}.',
  'class.change.tss': 'TSS: {from} til {to}.',
  'class.change.text': 'Navne, beskrivelse eller instruktioner blev ændret.',

  // Save, Update and the undecided write (ADR 0007 sections 5 to 8 and 12).
  'class.saveReason.write_unresolved':
    'En tidligere gemning eller opdatering af dette hold i MOWL er stadig uafklaret. Afgør den først med Tjek igen eller Gør det alligevel i feltet nedenfor, og prøv så igen.',
  'class.saveReason.call_may_be_running':
    'classbeat skriver måske stadig dette hold til MOWL. Vent et par minutter, og prøv så igen.',
  'class.saveReason.hash_mismatch':
    'Den version er ikke den, som siden viste: den blev ændret et andet sted. Siden er indlæst igen.',
  'class.saveReason.class_busy':
    'Dette hold er optaget af en anden opgave, måske fra en anden fane. Siden er indlæst igen. Vent, til den er færdig, og prøv så igen.',
  'class.saveReason.not_connected': 'MOWL er ikke tilsluttet. Tilslut det på din kontoside, og prøv så igen.',
  'class.saveReason.music_unlinked':
    'Musikken i denne version findes ikke i din tilsluttede MOWL-konto. Ændr den, og gem den nye version.',
  'class.saveReason.invalid_input':
    'Det kunne ikke gemmes. Siden er indlæst igen; tjek holdet, og prøv igen.',
  'class.saveReason.busy': 'Du har allerede opgaver i gang. Vent, til en er færdig, og prøv så igen.',
  'class.saveReason.idempotency_mismatch':
    'Den anmodning er allerede sendt med andet indhold. Genindlæs siden, og prøv igen.',
  'class.saveFailure.upstream_unavailable':
    'MOWL kunne ikke kontaktes. Der blev ikke skrevet noget. Prøv igen om et par minutter.',
  'class.saveFailure.rate_limited':
    'MOWL bad classbeat om at sætte tempoet ned. Der blev ikke skrevet noget. Vent et par minutter, og prøv igen.',
  'class.saveFailure.lease_lost':
    'Arbejdet blev afbrudt for mange gange. Hvis classbeat ikke kunne afgøre, om noget nåede MOWL, står det på listen nedenfor, så du kan afgøre det. Ellers blev der ikke skrevet noget; prøv igen.',
  'class.saveFailure.not_connected':
    'MOWL er ikke tilsluttet. Tilslut det på din kontoside, og prøv så igen.',
  'class.saveFailure.needs_reconnect':
    'MOWL accepterer ikke længere det gemte login. Tilslut det igen på din kontoside, og prøv så igen.',
  'class.saveFailure.key_unavailable':
    'Det gemte MOWL-login kunne ikke læses. Tilslut MOWL igen på din kontoside.',
  'class.saveFailure.invalid_input':
    'Denne version kunne ikke skrives til MOWL. Genindlæs siden, og prøv igen.',
  'class.saveFailure.operation_busy':
    'En anden opgave fra dig brugte stadig MOWL. Der blev ikke skrevet noget. Prøv igen om lidt.',
  'class.saveFailure.music_unlinked':
    'Musikken i denne version findes ikke i din tilsluttede MOWL-konto. Ændr den, og gem den nye version.',
  'class.saveFailure.program_changed':
    'Dette træningspas blev ændret i MOWL, efter at classbeat gemte det. Der blev ikke skrevet noget.',
  'class.saveFailure.program_missing':
    'Dette træningspas findes ikke længere i din MOWL-konto. Der blev ikke skrevet noget.',
  'class.saveFailure.program_other_sport':
    'Dette træningspas i MOWL er til en anden sport end dette hold, så classbeat erstattede det ikke. Der blev ikke skrevet noget.',
  'class.saveFailure.internal': 'Noget gik galt hos os. Prøv igen senere.',
  'class.saveFailure.default':
    'Jobbet blev ikke færdigt. Se i feltet nedenfor efter noget uafklaret, og prøv så igen.',
  'class.uncertain.save.1':
    'classbeat bad MOWL om at tilføje dette hold som et nyt træningspas og fik ikke et tydeligt svar.',
  'class.uncertain.save.2':
    'Træningspasset kan være i din MOWL-konto, helt eller kun delvist, eller det er der slet ikke. classbeat prøver ikke igen af sig selv, så du ikke ved et uheld får to kopier.',
  'class.uncertain.update.1':
    'classbeat bad MOWL om at opdatere det træningspas, der blev gemt tidligere, og fik ikke et tydeligt svar.',
  'class.uncertain.update.2':
    'Din MOWL-kopi kan være uændret, opdateret eller kun delvist opdateret. MOWL kan også have oprettet et nyt, tomt træningspas med samme navn under Mine træningspas; hvis du finder et, er det dit eget private træningspas, og du kan slette det i MOWL. classbeat prøver ikke igen af sig selv.',
  'class.warning.duplicate':
    'Gør det alligevel betyder, at du afgør, at træningspasset ikke blev gemt. Hvis det alligevel blev gemt, kan det her tilføje et andet træningspas til din MOWL-konto; du kan slette ekstra kopier i MOWL-appen.',
  'class.warning.overwrite':
    'Gør det alligevel betyder, at du afgør, at opdateringen ikke fandt sted. Din MOWL-kopi kan være delvist opdateret; den næste opdatering erstatter den med denne version.',
  'class.outcome.save.found': 'classbeat fandt træningspasset i din MOWL-konto og noterede det som gemt.',
  'class.outcome.save.nothing':
    'classbeat tjekkede, og træningspasset var ikke gemt. Du kan gemme det igen nu.',
  'class.outcome.save.still_unsure': 'classbeat kan stadig ikke afgøre, om træningspasset blev gemt.',
  'class.outcome.save.decided':
    'Forstået. Du kan gemme det igen nu; hvis det alligevel blev gemt, får du to kopier i MOWL.',
  'class.outcome.update.found': 'classbeat så, at dit MOWL-træningspas var opdateret, og noterede det.',
  'class.outcome.update.nothing':
    'classbeat tjekkede, og dit MOWL-træningspas er uændret. Du kan opdatere det igen nu.',
  'class.outcome.update.still_unsure': 'classbeat kan stadig ikke afgøre, om træningspasset blev opdateret.',
  'class.outcome.update.decided': 'Forstået. Den næste opdatering erstatter din MOWL-kopi med denne version.',
  'class.saved': 'Gemt. Træningspasset er i din MOWL-konto, privat.',
  'class.savedSoundless': 'Gemt uden musik. Træningspasset er i din MOWL-konto, privat.',
  'class.updated': 'Opdateret. Dit MOWL-træningspas svarer nu til denne version.',
  'class.updatedUnverified':
    'Opdateringen gik igennem, men classbeat kunne ikke bekræfte, at MOWL nu stemmer overens (det kan stadig have en playliste, som du tilføjede i appen). Tjek træningspasset i MOWL-appen.',

  // Pattern notes (ADR 0017 section 4); the sentences mirror the backend's rationales.
  'class.rule.warmup_short':
    'De fleste hold af denne slags cykler et par minutter før den første hårde indsats.',
  'class.rule.hard_start_late': 'De fleste hold af denne slags når den første hårde indsats før dette punkt.',
  'class.rule.cooldown_short':
    'De fleste hold af denne slags slutter med en let nedkøling på et par minutter.',
  'class.rule.cooldown_long': 'De fleste hold af denne slags holder nedkølingen kort.',
  'class.rule.hard_share_low': 'De fleste hold af denne slags bruger en god del af turen i hårde zoner.',
  'class.rule.hard_share_high':
    'De fleste hold af denne slags levner plads til lettere cykling mellem indsatserne.',
  'class.rule.zone_ceiling_exceeded': 'De fleste hold af denne slags holder sig væk fra de højeste zoner.',
  'class.rule.top_zone_share_low':
    'De fleste hold af denne slags indeholder noget cykling i de højeste zoner.',
  'class.rule.top_zone_share_high': 'De fleste hold af denne slags begrænser andelen i de højeste zoner.',
  'class.rule.mean_zone_low':
    'De fleste hold af denne slags har et jævnere anstrengelsesniveau samlet set end dette.',
  'class.rule.mean_zone_high':
    'De fleste hold af denne slags har et lavere anstrengelsesniveau samlet set end dette.',
  'class.rule.tss_rate_low':
    'De fleste hold af denne slags lægger lidt mere træningsbelastning i hvert minut.',
  'class.rule.tss_rate_high':
    'De fleste hold af denne slags lægger lidt mindre træningsbelastning i hvert minut.',
  'class.rule.hard_too_early':
    'De fleste hold af denne slags holder de hårde indsatser tilbage i holdets første fjerdedel.',
  'class.rule.standing_long': 'De fleste hold af denne slags begrænser andelen af stående cykling.',
  'class.rule.longest_interval_short': 'De fleste hold af denne slags har mindst ét længere interval.',
  'class.rule.longest_interval_long': 'De fleste hold af denne slags har alle intervaller kortere end dette.',
  'class.rule.median_interval_short': 'De fleste hold af denne slags bruger længere intervaller end dette.',
  'class.rule.median_interval_long': 'De fleste hold af denne slags bruger kortere intervaller end dette.',
  'class.rule.sprints_few': 'De fleste hold af denne slags lægger nogle flere sprints ind i turen.',
  'class.rule.sprints_many': 'De fleste hold af denne slags lægger færre sprints ind i turen.',
  'class.rule.climb_share_low': 'De fleste hold af denne slags bruger mere af turen på siddende stigninger.',
  'class.rule.climb_share_high':
    'De fleste hold af denne slags bruger mindre af turen på siddende stigninger.',
  'class.rule.work_rest_low': 'De fleste hold af denne slags arbejder hårdere for hvert minuts hvile.',
  'class.rule.work_rest_high': 'De fleste hold af denne slags hviler længere for hvert minuts hårdt arbejde.',
  'class.rule.intensity_factor_low':
    'De fleste hold af denne slags har en højere intensitet samlet set end dette.',
  'class.rule.intensity_factor_high':
    'De fleste hold af denne slags har en lavere intensitet samlet set end dette.',
  'class.notes.hereOnly': 'Dette hold: {here}',
  'class.notes.hereUsual': 'Dette hold: {here}. Almindeligt: {usual}',
  'class.notes.between': '{low} til {high}',
  'class.notes.orMore': '{low} eller mere',
  'class.notes.upTo': 'op til {high}',
  'class.notes.perMinute': '{value} TSS pr. minut',
  'class.notes.title': 'Mønsternoter',
  'class.notes.intro':
    'Hvad der er almindeligt på hold af denne slags. Din beskrivelse kommer først; disse noter blokerer aldrig noget.',
  'class.notes.hidden': 'Noten er skjult.',
  'class.notes.showAgain': 'Vis igen',
  'class.notes.showAgainAria': 'Vis igen: {note}',
  'class.notes.dismiss': 'Afvis',
  'class.notes.dismissAria': 'Afvis: {note}',
  'class.notes.dismissFailed': 'Noten kunne ikke afvises. Prøv igen om lidt.',
  'class.notes.showAgainFailed': 'Noten kunne ikke vises igen. Prøv igen om lidt.',
  'class.notes.matches': 'Passer til din beskrivelse',
  'class.notes.worth': 'Værd at se på',
  'class.notes.good': 'Godt at vide',
  'class.notes.explained': 'Din beskrivelse beder om dette, så det er kun en note.',
  'class.notes.currentRules': 'Noterne følger de aktuelle regler',

  // The class card, my classes and the class page.
  'class.card.tss': 'TSS {value}',
  // The screen-reader label of the small class profile (MiniProfile). Two forms (one, other), chosen by the interval count.
  'class.profile.label': {
    one: 'Holdprofil, {length}, {count} interval',
    other: 'Holdprofil, {length}, {count} intervaller',
  },
  'class.profile.empty': 'Ingen holdprofil endnu',
  'class.list.title': 'Mine hold',
  'class.list.design': 'Design et hold',
  'class.list.loading': 'Indlæser dine hold',
  'class.list.failed': 'Dine hold kunne ikke indlæses.',
  'class.list.none': 'Ingen hold endnu',
  'class.list.noneText': 'Beskriv et hold, så designer classbeat det ud fra din playliste.',
  'class.list.show': 'Vis',
  'class.list.filterAll': 'Alle',
  'class.list.filterSaved': 'Gemt i MOWL',
  'class.list.filterDrafts': 'Udkast',
  'class.list.search': 'Søg i dine hold',
  'class.list.noMatch': 'Intet hold passer. Ændr filteret eller søgningen.',
  'class.page.deletedTitle': 'Holdet er slettet',
  'class.page.deleted': 'Holdet, dets versioner og din samtale er væk fra classbeat.',
  'class.page.loadingTitle': 'Et øjeblik',
  'class.page.loading': 'Indlæser dit hold',
  'class.page.notFoundTitle': 'Holdet blev ikke fundet',
  'class.page.notFound': 'Dette hold findes ikke, eller det er ikke dit.',
  'class.page.failedTitle': 'Holdet kunne ikke indlæses',
  'class.page.failed': 'Serveren kunne ikke kontaktes eller svarede ikke som forventet.',
  'class.page.running': 'Løb',
  'class.page.noMusic': 'Ingen musik',
  'class.page.changeSongs': 'Skift sange',
  'class.page.moreActions': 'Flere handlinger',
  'class.page.deleteClass': 'Slet hold',
  'class.page.job.waiting': 'Sangene venter på din godkendelse.',
  'class.page.job.design':
    'Dette hold bliver designet. Det kan tage et par minutter, og du kan forlade siden.',
  'class.page.job.refit': 'Dine tidsrettelser bliver anvendt.',
  'class.page.job.songs': 'Der arbejdes på sangene på dette hold.',
  'class.page.job.review': 'Gennemgå sangene',
  'class.page.job.progress': 'Se, hvordan det går',
  'class.page.playlist': 'Din Spotify-playliste: {name}',
  'class.page.openSpotify': 'Åbn i Spotify',
  'class.page.noVersion': 'Dette hold har endnu ingen version.',
  'class.page.notCurrent':
    'Du ser på version {viewing}. Holdet bruger version {current}, og ændringer tager udgangspunkt i den.',
  'class.page.back': 'Tilbage til version {number}',
  'class.page.loadingVersion': 'Indlæser versionen',
  'class.page.versionFailed': 'Denne version kunne ikke indlæses. Genindlæs siden, og prøv igen.',
  'class.side.label': 'Gem, ændr og versioner',
  'class.songStrip.label': 'Sange',
  'class.songStrip.title': '{title}, {bpm} BPM',

  // Delete class (ADR 0007 section 11).
  'class.delete.title': 'Slet hold',
  'class.delete.uncertain':
    'classbeat er ikke sikker på, om en gemning af dette hold i MOWL gik igennem. Hvis du sletter holdet nu, forsvinder den eneste registrering af den.',
  'class.delete.warning':
    'Det her sletter holdet, alle dets versioner og din samtale fra classbeat. Det kan ikke fortrydes.',
  'class.delete.kept': {
    one: 'Det træningspas, som classbeat gemte i din MOWL-konto, bliver der; slet det i MOWL.',
    other: 'De {count} træningspas, som classbeat gemte i din MOWL-konto, bliver der; slet dem i MOWL.',
  },
  'class.delete.keptNone':
    'Alle træningspas, som classbeat gemte i din MOWL-konto, bliver der; slet dem i MOWL.',
  'class.delete.anyway': 'Slet alligevel',
  'class.delete.confirm': 'Ja, slet dette hold',
  'class.delete.keep': 'Behold det',

  // Duplicate a class.
  'class.duplicate.label': 'Duplikér',
  'class.duplicate.trigger': '{label} {title}',
  'class.duplicate.title': 'Duplikér dette hold',
  'class.duplicate.descriptionVersion':
    'Version {number} bliver version 1 af et nyt hold. Det er ikke gemt i MOWL og har endnu ingen godkendte sange eller samtale. Det tæller som ét hold denne måned.',
  'class.duplicate.descriptionThis':
    'Denne version bliver version 1 af et nyt hold. Det er ikke gemt i MOWL og har endnu ingen godkendte sange eller samtale. Det tæller som ét hold denne måned.',
  'class.duplicate.name': 'Kopiens navn',
  'class.duplicate.sending': 'Duplikerer…',

  // Words the class-page helpers return (lib/profile.ts, lib/classCards.ts, api/classHistory.ts, api/classDuplicate.ts; ML5e).
  'class.zoneName.recovery': 'Restitution',
  'class.zoneName.endurance': 'Udholdenhed',
  'class.zoneName.tempo': 'Tempo',
  'class.zoneName.threshold': 'Tærskel',
  'class.zoneName.vo2max': 'VO2max',
  'class.zoneName.anaerobic': 'Anaerob',
  'class.zoneName.neuromuscular': 'Neuromuskulær',
  'class.incline.raised': 'Hældning',
  'class.incline.flat': 'Flad',
  'class.tss.approximate': 'omtrentlig: zone 7 har ingen præcis TSS-vægt',
  'class.length.minutes': '{minutes} min',
  'class.length.hoursMinutes': '{hours} t {minutes} min',
  'class.status.designing': 'Designes',
  'class.status.changing': 'Ændres',
  'class.status.busy': 'Optaget',
  'class.status.noVersion': 'Ingen version endnu',
  'class.status.unsavedChanges': 'Version {number} har ændringer, der endnu ikke er i MOWL',
  'class.status.saved': 'Version {number} gemt i MOWL',
  'class.status.notSaved': 'Version {number}, ikke gemt i MOWL',
  'class.compare.gone': 'En af disse versioner findes ikke længere.',
  'class.compare.unreadable': 'En af disse versioner kan ikke længere læses, så de to kan ikke sammenlignes.',
  'class.compare.failed': 'De to versioner kunne ikke sammenlignes. Prøv igen.',
  'class.asked.duplicated': 'Duplikeret fra »{title}«',
  'class.asked.duplicatedVersion': 'Duplikeret fra »{title}«, version {number}',
  'class.asked.duplicatedGone': 'Duplikeret fra et hold, der ikke findes mere',
  'class.asked.request': 'Du bad om: »{request}«',
  'class.asked.changed': 'Ændret efter ønske',
  'class.asked.songs': 'Sange ændret',
  'class.asked.first': 'Første design',
  'class.compare.segments': 'Segmenter: {added} tilføjet, {removed} fjernet.',
  'class.compare.intervalsChanged': { one: '{count} interval ændret', other: '{count} intervaller ændret' },
  'class.compare.intervalsAdded': { one: '{count} interval tilføjet', other: '{count} intervaller tilføjet' },
  'class.compare.intervalsRemoved': { one: '{count} interval fjernet', other: '{count} intervaller fjernet' },
  'class.compare.intervals': '{parts}.',
  'class.compare.lengthLonger': 'Længde: {from} til {to} ({diff} længere).',
  'class.compare.lengthShorter': 'Længde: {from} til {to} ({diff} kortere).',
  'class.compare.lengthSame': 'Længde: den samme, {length}.',
  'class.compare.tssSame': 'TSS: den samme, {tss}.',
  'class.compare.tss': 'TSS: {from} til {to}.',
  'class.compare.timing': 'Timingadvarsler: {from} til {to}.',
  'class.compare.musicSame': 'Musik: den samme.',
  'class.compare.musicSongs': 'Musik: samme kilde, men sangene blev ændret.',
  'class.compare.musicDifferent': 'Musik: anden musik.',
  'class.compare.onlyText': 'Kun navne, beskrivelser eller instruktioner er forskellige.',
  'class.compare.same': 'Træningspassene er ens.',
  'class.compare.textToo': 'Nogle navne, beskrivelser eller instruktioner er også forskellige.',
  'class.duplicate.copyOf': '{title} (kopi)',
  'class.duplicate.reason.not_ready':
    'Dette hold har endnu intet færdigt design, så der er intet at kopiere. Prøv igen, når det er klar.',
  'class.duplicate.reason.feature_off': 'Hold som dette kan ikke oprettes lige nu, så det kan ikke kopieres.',
  'class.duplicate.reason.soundless_not_evaluated':
    'Hold uden musik er endnu ikke tilgængelige på din plan, så dette kan ikke kopieres.',
  'class.duplicate.reason.not_changeable':
    'Dette hold har ingen egen musik, så det kan ikke ændres via chatten. Du kan stadig se, bruge og gemme dets versioner.',
  'class.duplicate.reason.hash_mismatch':
    'Den version er ikke den, som siden viste: den blev ændret et andet sted. Genindlæs siden, og prøv igen.',
  'class.duplicate.reason.not_found': 'Dette hold, eller den version, findes ikke længere.',
  'class.duplicate.reason.invalid_input': 'Det navn kan ikke bruges. Giv kopien et navn på 1 til 100 tegn.',
  'class.duplicate.reason.idempotency_mismatch':
    'Denne kopi er allerede bestilt med andre oplysninger. Luk det her, og prøv igen.',
  'class.duplicate.reason.internal': 'Denne version kan ikke længere læses, så den kan ikke kopieres.',
  // Shown on the account page and on the undecided-attach panel (components/account/mowlWarning.ts).
  'account.forgetWarning':
    'classbeat glemmer, hvilke playlister det har tilføjet til din MOWL-musik, og alle tilføjelser, der blev efterladt uafklarede. Det næste hold, du designer ud fra en af de playlister, kan tilføje den til din MOWL-musik endnu en gang, så du kan se to kopier der.',

  // Draft summary and revision view.
  'class.draft.summary': 'Udkast {number}, {total} i alt',
  'class.revision.heading': 'Version {number}, {origin}',
  'class.revision.length': 'Længde',
  'class.revision.tss': 'TSS',
  'class.revision.segments': 'Segmenter',
  'class.revision.songs': 'Sange',
  'class.revision.hardest': 'Hårdeste zone',
  'class.revision.musicChanged':
    'Musikken blev ændret siden versionen før: playlisten i MOWL er ikke den, som dette hold blev bygget på, så tjek, at sangene stadig passer.',
  'class.revision.songBySong': 'Sang for sang',
  'class.zoneTime.title': 'Tid i hver zone',
  'class.chart.summary':
    'Anstrengelse over tid for {title}: {segments} segmenter, {total} i alt. Den hårdeste zone er {zone}.',
  'class.chart.summaryRunning':
    'Anstrengelse over tid for {title}: {segments} segmenter, {total} i alt. Den hårdeste zone er {zone}. Hældningen for hvert interval vises, når du vælger det.',
  'class.chart.keys': '{title}: brug venstre- og højrepilene for at se på hvert interval',
  'class.chart.minutes': '{minutes} min',
  'class.chart.select':
    'Vælg en søjle, eller sæt fokus på diagrammet og brug piletasterne, for at se et interval.',
  'class.chart.zoneName': 'Zone {zone} {name}',
  'class.chart.range': '{from} til {to}',

  // Versions and their history.
  'class.revisions.none': 'Der er endnu ingen version.',
  // One form on purpose: the English wording is "{count} segments" for every count, so no plural is needed.
  'class.revisions.segments': '{count} segmenter',
  'class.history.unavailable': 'Versionshistorikken kunne ikke læses, så kun den enkle liste vises.',
  'class.history.pick': 'Vælg to versioner for at sammenligne dem.',
  'class.history.cannotChange':
    'Ændr det er ikke tilgængeligt for dette hold lige nu: det har ingen egen musik, eller den slags hold er slået fra. Du kan stadig se, sammenligne, bruge og gemme dets versioner.',
  'class.history.unreadable': {
    one: '{count} tidligere version kan ikke længere læses og vises ikke på listen.',
    other: '{count} tidligere versioner kan ikke længere læses og vises ikke på listen.',
  },
  'class.history.list': 'Versionshistorik',
  'class.history.savedMowl': 'Gemt i MOWL',
  'class.history.savedEarlier': 'Gemt i en tidligere MOWL-forbindelse',
  'class.history.fromSongs': 'Godkendte sange',
  'class.history.tss': 'TSS {value}',
  'class.history.compare': 'Sammenlign',
  'class.history.compareAria': 'Sammenlign version {number}',
  'class.history.compareButton': 'Sammenlign versionerne {older} og {newer}',
  'class.history.compareTitle': 'Version {older} til version {newer}',

  // Beat 1 corrections (ADR 0007 amendment "beat-one corrections").
  'class.beat.asAnalysed': 'som analyseret',
  'class.beat.later': { one: '{count} beat senere', other: '{count} beat senere' },
  'class.beat.earlier': { one: '{count} beat tidligere', other: '{count} beat tidligere' },
  'class.beat.song': 'Sang {song}',
  'class.beat.title': 'Beat 1',
  'class.beat.intro':
    'Lyt til holdet. Hvis ændringerne i en sang kommer et beat for tidligt, så flyt dens beat 1 senere; hvis de kommer for sent, så flyt det tidligere. En takt har fire beat, så ét trin er en fjerdedel af en takt.',
  'class.beat.from': 'Fra {start}, {bpm} BPM',
  'class.beat.bpmChanged': 'Tempoet på denne sang blev ændret, siden du satte dens beat 1: lyt igen.',
  'class.beat.estimate': 'Beat 1 er et skøn her: lyt til den første ændring i denne sang.',
  'class.beat.group': 'Beat 1 for {name}',
  'class.beat.moveEarlier': 'Flyt beat 1 for {name} tidligere',
  'class.beat.moveLater': 'Flyt beat 1 for {name} senere',
  'class.beat.reset': 'Nulstil beat 1 for {name}',
  'class.beat.earlierButton': 'Tidligere',
  'class.beat.laterButton': 'Senere',
  'class.beat.resetButton': 'Nulstil',
  'class.beat.stale':
    'Disse tidsrettelser gælder musik, som denne version ikke spiller: den blev analyseret igen eller ændret, eller sangen er ikke i denne version. De gemmes og tæller ikke med i grænsen, så hvis du gør en tidligere version aktuel, kan de bruges igen. Ryd dem, hvis du ikke længere har brug for dem.',
  'class.beat.staleRow': 'Sang {song}: {beats}',
  'class.beat.clearAria': 'Ryd den tidligere tidsrettelse for sang {song}',
  'class.beat.clear': 'Ryd',
  'class.beat.clearAll': 'Ryd alle tidsrettelser',
  'class.beat.pending':
    'Denne version bruger endnu ikke dine tidsrettelser. Anvend dem for at lave en ny version, hvor ændringerne er flyttet til de rettede beat; gem eller opdater den i MOWL bagefter.',
  'class.beat.apply': 'Anvend tidsrettelser',

  // The Change it chat (ADR 0007 section 3).
  'class.chat.title': 'Ændr det',
  'class.chat.noLonger': 'Denne ændring findes ikke længere.',
  'class.chat.addingPlaylist': 'Tilføjer din playliste til din MOWL-musik.',
  'class.chat.stop': 'Stop ændringen',
  'class.chat.unfinished': 'Denne anmodning blev ikke færdig. Intet blev ændret.',
  'class.chat.checking': 'Tjekker, hvad der skete',
  'class.chat.askAgain': 'Du kan bede om ændringen igen nu.',
  'class.chat.changeSongs': 'Skift sange',
  'class.chat.stopped': 'Denne ændring blev stoppet. Intet blev ændret.',
  'class.chat.empty':
    'Der er endnu ikke bedt om noget. Sig, hvad der skal ændres, så ændrer classbeat dette hold.',
  'class.chat.requests': 'Dine ønsker',
  'class.chat.youAsked': 'Du bad om',
  'class.chat.changed': 'classbeat ændrede det',
  'class.chat.changedVersion': 'classbeat ændrede det, version {number}',
  'class.chat.musicChanged': 'Musikken blev ændret: playlisten i MOWL er ikke den, der var der før.',
  'class.chat.designerNote': 'Note fra designeren:',
  'class.chat.viewVersion': 'Vis version {number}',
  'class.chat.writeSomething': 'Skriv, hvad du vil have ændret.',
  'class.chat.tooLong': 'En anmodning må højst være på {max} tegn; denne er på {count}.',
  'class.chat.noVersion': 'Der er endnu ingen version at ændre.',
  'class.chat.unavailable':
    'Ændr det er ikke tilgængeligt for dette hold lige nu. Du kan stadig se, sammenligne, bruge og gemme dets versioner.',
  'class.chat.busy':
    'Dette hold er optaget af en anden opgave. Du kan bede om en ændring, når den er færdig.',
  'class.chat.form': 'Bed om en ændring',
  'class.chat.label': 'Hvad skal ændres?',
  'class.chat.hint':
    'For eksempel: gør den anden stigning længere, eller tilføj en sprint før nedkølingen. Højst {max} tegn.',

  // Save to MOWL and Update (ADR 0007 sections 5 to 9).
  'class.save.title': 'Gem i MOWL',
  'class.save.runSave': 'Gemmer i MOWL:',
  'class.save.runUpdate': 'Opdaterer i MOWL:',
  'class.save.stop': 'Stop',
  'class.save.checking': 'Opdateret. Tjekker, hvad MOWL beholdt…',
  'class.save.uncertain':
    'classbeat kunne ikke afgøre, om MOWL modtog det. Læs feltet nedenfor, og afgør så.',
  'class.save.updateAnyway': 'Opdater alligevel (erstatter dine ændringer i MOWL)',
  'class.save.stopped':
    'Dette blev stoppet, før der blev skrevet noget, eller også viser classbeat det nedenfor.',
  'class.save.detachedLine':
    'Dette hold blev tidligere gemt i en tidligere MOWL-forbindelse. Det træningspas er ikke længere koblet til, så det kan ikke opdateres. Gem tilføjer et nyt til din nuværende MOWL-konto.',
  'class.save.notSaved': 'Endnu ikke gemt i MOWL.',
  'class.save.upToDate': 'Gemt i MOWL som version {saved}. Den er opdateret.',
  'class.save.behind':
    'Gemt i MOWL som version {saved}. Den aktuelle version ({current}) har ændringer, der endnu ikke er gemt.',
  'class.save.detachedOther':
    'Et andet træningspas blev tidligere gemt i en tidligere MOWL-forbindelse og er ikke længere koblet til.',
  'class.save.noVersion': 'Der er endnu ingen version at gemme.',
  'class.save.lost':
    'classbeat kan ikke længere læse den opgave, så det kan ikke afgøre, hvordan den endte. Linjen ovenfor viser, hvad MOWL har for dette hold nu; tjek den, før du prøver igen.',
  'class.save.again': 'Gem igen',
  'class.save.asNew': 'Gem som et nyt træningspas',
  'class.save.updateAgain': 'Opdater igen',
  'class.save.update': 'Opdater gemt træningspas',
  'class.save.confirmSave':
    'classbeat tilføjer version {number} af dette hold til din MOWL-konto som et nyt træningspas. Det er privat: kun du kan se det.',
  'class.save.confirmSaveSoundless':
    'classbeat tilføjer version {number} af dette hold til din MOWL-konto som et nyt træningspas uden musik. Det er privat: kun du kan se det.',
  'class.save.confirmSaveMore': 'Et nyt træningspas tilføjes; det, du gemte tidligere, bliver, som det er.',
  'class.save.confirmUpdate':
    'classbeat læser først træningspasset i din MOWL-konto. Hvis du har ændret det i MOWL, efter at classbeat gemte det, stopper classbeat og skriver ingenting. Ellers erstatter det træningspasset med version {number}. Det forbliver privat, i din MOWL-konto.',
  'class.save.confirmUpdateSoundless':
    'classbeat læser først træningspasset i din MOWL-konto. Hvis du har ændret det i MOWL, efter at classbeat gemte det, stopper classbeat og skriver ingenting. Ellers erstatter det træningspasset med version {number}, uden musik. Det forbliver privat, i din MOWL-konto.',
  'class.save.overwriteTitle': 'Opdater alligevel?',
  'class.save.overwriteText':
    'Det her erstatter træningspasset i din MOWL-konto med version {number}, inklusive alle ændringer, du har lavet i det i MOWL, eller hvad en skrivning, der ikke blev færdig, efterlod der. Det kan ikke fortrydes. Træningspasset forbliver privat.',
  'class.save.updateButton': 'Opdater træningspas',
  'class.save.replace': 'Ja, erstat det',
  'class.save.updateUnavailable':
    'Det er endnu ikke muligt at opdatere det gemte træningspas. Gem tilføjer i stedet et nyt træningspas.',

  // Undecided writes and the undecided playlist attach (ADR 0005 section 7, ADR 0007 section 8).
  'class.write.title.save': 'classbeat kunne ikke afgøre, om dit træningspas blev gemt',
  'class.write.title.update': 'classbeat kunne ikke afgøre, om dit træningspas blev opdateret',
  'class.write.waiting':
    'classbeat sender måske stadig dette til MOWL, så Tjek igen og Gør det alligevel venter til {time}.',
  'class.write.running':
    'classbeat skriver måske stadig til MOWL, så det kan ikke afgøres endnu. Vent et par minutter.',
  'class.write.settled': 'Det var allerede afgjort. Listen er opdateret.',
  'class.write.notSaved': 'Ja, det blev ikke gemt',
  'class.write.notUpdated': 'Ja, det blev ikke opdateret',
  'class.write.loadFailed': 'Det, der er tilbage at afgøre for dette hold, kunne ikke indlæses.',
  'class.write.heading': 'Uklart, hvad der nåede MOWL',
  'class.attach.title': 'classbeat kunne ikke afgøre, om din playliste blev tilføjet',
  'class.attach.intro':
    'For at læse din musik bad classbeat MOWL om at tilføje den playliste, du valgte, til din MOWL-musik. MOWL svarede ikke tydeligt, så playlisten kan være der nu eller ikke. classbeat prøver ikke igen af sig selv, så du ikke ved et uheld får to kopier.',
  'class.attach.found': 'Playlisten er i din MOWL-musik. Du kan designe holdet igen nu.',
  'class.attach.absent': 'Playlisten er ikke i din MOWL-musik. Du kan designe holdet igen nu.',
  'class.attach.still_unsure': 'classbeat kan stadig ikke afgøre, om playlisten blev tilføjet.',
  'class.attach.decided':
    'Forstået. Det næste design tilføjer playlisten til din MOWL-musik igen, hvilket kan efterlade to kopier der.',
  'class.attach.settled': 'Det var allerede afgjort. Listen nedenfor er opdateret.',
  'class.attach.loadFailed': 'Det, der er tilbage at afgøre, kunne ikke indlæses.',
  'class.attach.none':
    'classbeat har intet trin tilbage, som du skal afgøre her, så det kan ikke tilbyde Tjek igen eller Gør det alligevel. Hvis du vil være sikker på, at playlisten ikke bliver tilføjet to gange, så se i din MOWL-musik. For at rydde classbeats registrering af den skal du afbryde MOWL og tilslutte det igen på {account} og derefter starte et nyt design fra formularen.',
  'class.attach.accountPage': 'din kontoside',
  'class.attach.earlier': 'Dette trin stammer fra et tidligere design af dig.',
  'class.attach.warning':
    'Gør det alligevel betyder, at du afgør, at playlisten ikke blev tilføjet. Det næste design tilføjer den til din MOWL-musik igen. Hvis den alligevel blev tilføjet, får du to kopier af den der.',
  'class.attach.addAgain': 'Ja, tilføj den igen',
  'common.tryAgain': 'Prøv igen',
  'newclass.title': 'Design et hold',
  'newclass.loading': 'Indlæser din MOWL-musik',
  'newclass.noMowl.reconnect':
    'MOWL accepterer ikke længere det gemte login. {link}, og kom derefter tilbage hertil.',
  'newclass.noMowl.connect': 'classbeat læser din musik fra MOWL. {link}, og kom derefter tilbage hertil.',
  'newclass.noMowl.reconnectLink': 'Tilslut MOWL igen',
  'newclass.noMowl.connectLink': 'Tilslut MOWL på din kontoside',
  'newclass.playlistError.unavailable': 'MOWL kunne ikke nås. Prøv igen om nogle minutter.',
  'newclass.playlistError.rateLimited':
    'MOWL bad classbeat om at sætte tempoet ned. Prøv igen om nogle minutter.',
  'newclass.playlistError.default':
    'Dine playlister kunne ikke indlæses fra MOWL. Prøv igen om nogle minutter.',
  'newclass.createError.not_connected': 'MOWL er ikke tilsluttet. Tilslut det først på din kontoside.',
  'newclass.createError.class_busy': 'Dette hold har allerede et design i gang.',
  'newclass.createError.idempotency_mismatch': 'Det virkede ikke. Tjek formularen, og prøv igen.',
  'newclass.createError.feature_off':
    'Løbehold er ikke tilgængelige lige nu. Skift til Cykling, eller prøv igen senere.',
  'newclass.createError.soundless_not_evaluated':
    'Hold uden musik er ikke klar til din plan endnu. Vælg en playliste, eller prøv igen senere.',
  'newclass.createError.running_not_evaluated':
    'Løbehold er ikke tilgængelige på din plan endnu. Intet blev trukket. Skift til Cykling, eller prøv igen senere.',
  'newclass.createError.invalid_input':
    'Holdet blev ikke accepteret. Tjek felterne; stilen og beskrivelsen tilsammen kan være over {bytes} byte.',
  'newclass.createError.soundlessOff':
    'Hold uden musik er ikke tilgængelige lige nu. Vælg en playliste, eller prøv igen senere.',
  'newclass.planMaxLength': 'Din plan tillader hold på op til {count} minutter.',
  'newclass.planLeavesOut': 'Din plan omfatter ikke {list}.',
  'newclass.planLeavesOut.races': 'race',
  'newclass.planLeavesOut.ftpTests': 'FTP-test',
  'newclass.list.and': '{head} og {last}',
  'newclass.invalid.name': 'Giv holdet et navn på højst 100 tegn.',
  'newclass.invalid.playlist': 'Vælg en playliste.',
  'newclass.invalid.mix': 'Vælg et Swedebeat-mix.',
  'newclass.invalid.length': 'Længden er 10 til 180 minutter.',
  'newclass.invalid.climbs': 'Antallet af stigninger er 0 til 10.',
  'newclass.invalid.sprints': 'Antallet af sprints er 0 til 20.',
  'newclass.invalid.races': 'Antallet af race er 0 til 5.',
  'newclass.invalid.style': 'Stilen må højst være 60 tegn.',
  'newclass.invalid.notes': 'Beskrivelsen må højst være 2000 tegn.',
  'newclass.invalid.briefTooLong':
    'Stilen og beskrivelsen er for lange til ét hold (grænsen er 4096 byte i alt, og bogstaver med accent eller uden for det latinske alfabet tæller for mere end ét). Gør dem kortere, og prøv igen.',
  'newclass.minutes': '{count} min',
  'newclass.notSet': 'Ikke angivet',
  'newclass.untitledMix': 'Unavngivet mix',
  'newclass.untitledPlaylist': 'Unavngiven playliste',
  'newclass.summary.length': 'Længde',
  'newclass.summary.music': 'Musik',
  'newclass.summary.noMusic': 'Ingen musik',
  'newclass.summary.songsPicked': 'Sange udvalgt til dig',
  'newclass.summary.noMix': 'Intet mix valgt endnu',
  'newclass.summary.noPlaylist': 'Ingen playliste valgt endnu',
  'newclass.noPlaylists.title': 'Ingen playlister fundet',
  'newclass.noPlaylists.body':
    'MOWL viser ingen Spotify-playlister for din konto. Tilføj en i Intelligent Cycling-appen, og genindlæs derefter denne side.',
  'newclass.formLabel': 'Nyt hold',
  'newclass.basics': 'Det grundlæggende',
  'newclass.sport': 'Sport',
  'newclass.sport.cycling': 'Cykling',
  'newclass.sport.running': 'Løb',
  'newclass.sport.runningHint':
    'Et løbehold designes til en playliste, der allerede findes i din MOWL-konto. Hvert interval har en anstrengelseszone fra 1 til 5 og en indstilling for fladt eller hældning.',
  'newclass.className': 'Holdets navn',
  'newclass.classLength': 'Holdets længde',
  'newclass.commonLengths': 'Almindelige længder',
  'newclass.otherLength': 'Anden længde',
  'newclass.lengthMinutes': 'Længde i minutter',
  'newclass.music': 'Musik',
  'newclass.theRun': 'Løbeturen',
  'newclass.theRide': 'Cykelturen',
  'newclass.climbs': 'Stigninger',
  'newclass.climbs.fewer': 'Færre stigninger',
  'newclass.climbs.more': 'Flere stigninger',
  'newclass.sprints': 'Sprints',
  'newclass.sprints.fewer': 'Færre sprints',
  'newclass.sprints.more': 'Flere sprints',
  'newclass.races': 'Race',
  'newclass.races.fewer': 'Færre race',
  'newclass.races.more': 'Flere race',
  'newclass.stepper.range': '{min} til {max}',
  'newclass.ftp': 'FTP-test',
  'newclass.ftp.none': 'Ingen',
  'newclass.ftp.minutes': '{count} minutter',
  'newclass.notes': 'Noget andet?',
  'newclass.notes.hint': 'Fortæl classbeat, hvordan holdet skal føles.',
  'newclass.notes.hintNoGames':
    'Fortæl classbeat, hvordan holdet skal føles. Spil er ikke en del af din plan.',
  'newclass.side.title': 'Dit hold indtil videre',
  'newclass.side.sketchNote': 'En grov skitse af dine tal. Claude designer det rigtige hold.',
  'newclass.side.classesLeft': 'Bruger 1 af dine {count} resterende hold denne måned',
  'newclass.side.noClassesLeft': 'Du har ingen hold tilbage denne måned.',
  'newclass.side.submit': 'Design mit hold',
  'newclass.sketch': 'En grov skitse af dit hold',
  'newclass.sketch.none': 'Ingen skitse endnu',
  'newclass.effort': 'Anstrengelse',
  'newclass.effort.easy': 'Let',
  'newclass.effort.moderate': 'Moderat',
  'newclass.effort.hard': 'Hård',
  'newclass.mix.none':
    'MOWL viser endnu ingen Swedebeat-mix for din konto. Opret et mix i Swedebeat-appen, lad det synkronisere til Intelligent Cycling, og genindlæs derefter denne side.',
  'newclass.mix.label': 'Swedebeat-mix',
  'newclass.mix.analysed': 'Analyseret før',
  'newclass.mix.hint':
    'classbeat føjer dette mix til din MOWL-musik, så classbeat kan analysere lyden og læse sangene. Den første analyse kan tage omkring et minut.',
  'newclass.playlist.label': 'Playliste',
  'newclass.playlist.hint':
    'classbeat føjer denne playliste til din MOWL-musik, så classbeat kan læse sangene. Den forbliver privat for dig.',
  'newclass.style.suggestions': 'Stilforslag',
  'newclass.style.label': 'Stil',
  'newclass.style.hint': 'For eksempel: bakker, intervaller, udholdenhed.',
  'newclass.style.80s': '80’erne',
  'newclass.style.90s': '90’erne',
  'newclass.style.2000s': '00’erne',
  'newclass.style.pop': 'Pop',
  'newclass.style.rock': 'Rock',
  'newclass.style.dance': 'Dance',
  'newclass.style.hiphop': 'Hiphop',
  'newclass.style.latin': 'Latin',
  'job.failure.upstream_unavailable.title': 'MOWL kunne ikke nås',
  'job.failure.upstream_unavailable.detail':
    'classbeat prøvede flere gange. Dit hold blev ikke designet. Prøv igen om nogle minutter.',
  'job.failure.rate_limited.title': 'MOWL bad classbeat om at sætte tempoet ned',
  'job.failure.rate_limited.detail': 'Dit hold blev ikke designet. Vent nogle minutter, og prøv igen.',
  'job.failure.model_unavailable.title': 'Designeren har travlt',
  'job.failure.model_unavailable.detail':
    'Tjenesten, der designer holdet, kunne ikke tage imod anmodningen. Prøv igen om nogle minutter.',
  'job.failure.lease_lost.title': 'Designet blev afbrudt',
  'job.failure.lease_lost.detail': 'Det blev afbrudt for mange gange. Prøv igen.',
  'job.failure.operation_busy.title': 'Et andet design bruger denne playliste',
  'job.failure.operation_busy.detail':
    'Et andet design fra dig var stadig i gang med at føje den samme playliste til MOWL. Prøv igen om lidt.',
  'job.failure.not_connected.title': 'MOWL er ikke tilsluttet',
  'job.failure.not_connected.detail': 'Tilslut MOWL på din kontoside, og start derefter forfra.',
  'job.failure.needs_reconnect.title': 'MOWL accepterer ikke længere det gemte login',
  'job.failure.needs_reconnect.detail': 'Tilslut MOWL igen på din kontoside, og start derefter forfra.',
  'job.failure.key_unavailable.title': 'Det gemte MOWL-login kunne ikke læses',
  'job.failure.key_unavailable.detail': 'Tilslut MOWL igen på din kontoside, og start derefter forfra.',
  'job.failure.invalid_input.title': 'Anmodningen kunne ikke bruges',
  'job.failure.invalid_input.detail': 'Tjek formularen, og start forfra.',
  'job.failure.account_disabled.title': 'Din konto kan ikke starte designs lige nu',
  'job.failure.account_disabled.detail': 'Kontakt personen, der inviterede dig.',
  'job.failure.model_refused.title': 'Designeren afviste denne beskrivelse',
  'job.failure.model_refused.detail': 'Ret beskrivelsen, og start forfra.',
  'job.failure.model_config.title': 'classbeat kan ikke designe hold lige nu',
  'job.failure.model_config.detail': 'Det er et problem hos os. Prøv igen senere.',
  'job.failure.invalid_output.title': 'Designeren lavede ikke et brugbart hold',
  'job.failure.invalid_output.detail': 'Prøv igen, eller gør beskrivelsen enklere.',
  'job.failure.limit_exceeded.title': 'Denne beskrivelse krævede mere arbejde, end et design må bruge',
  'job.failure.limit_exceeded.detail': 'Prøv et kortere eller enklere hold.',
  'job.failure.outbound_blocked.title': 'Noget gik galt hos os',
  'job.failure.outbound_blocked.detail': 'Prøv igen senere.',
  'job.failure.program_changed.title': 'Dette træningspas blev ændret i MOWL',
  'job.failure.program_changed.detail': 'Dette træningspas blev ændret i MOWL, efter at classbeat gemte det.',
  'job.failure.program_missing.title': 'Dette træningspas findes ikke længere i MOWL',
  'job.failure.program_missing.detail': 'Dette træningspas findes ikke længere i din MOWL-konto.',
  'job.failure.music_changed.title': 'Musikken blev ændret',
  'job.failure.music_changed.detail':
    'Playlisten eller mixet er ikke det, som denne version blev lavet på, så tidsrettelserne blev ikke anvendt. Bed om en ændring i chatten for at designe efter musikken, som den er nu.',
  'job.failure.program_other_sport.title': 'Dette træningspas er til en anden sport',
  'job.failure.program_other_sport.detail':
    'Dette træningspas i MOWL er til en anden sport end dette hold, så classbeat erstattede det ikke.',
  'job.failure.use_change_songs.title': 'Denne anmodning handler kun om sangene',
  'job.failure.use_change_songs.detail':
    'Intet blev ændret. Brug Skift sange på holdsiden for at vælge andre.',
  'job.failure.media_host_not_allowed.title': 'Nye Swedebeat-mix kan ikke analyseres her endnu',
  'job.failure.media_host_not_allowed.detail':
    'Mixet blev føjet til din MOWL-konto, men det kunne ikke analyseres. Mix, du har analyseret før, virker stadig. Prøv et af dem eller en anden slags musik.',
  'job.failure.media_refused.title': 'Lyden fra dette mix kunne ikke hentes',
  'job.failure.media_refused.detail': 'Intet blev designet. Prøv igen senere.',
  'job.failure.analysis_failed.title': 'Lyden fra dette mix kunne ikke analyseres',
  'job.failure.analysis_failed.detail': 'Prøv et andet mix.',
  'job.failure.feature_off.title': 'Det er slået fra lige nu',
  'job.failure.feature_off.detail': 'Intet blev designet. Prøv igen senere.',
  'job.failure.analysis_limit.title': 'For mange nye Swedebeat-mix i dag',
  'job.failure.analysis_limit.detail':
    'Du har analyseret 10 nye mix inden for de seneste 24 timer. Mix, der er analyseret før, virker stadig; prøv et nyt i morgen.',
  'job.failure.soundless_not_evaluated.title': 'Hold uden musik er ikke klar til din plan endnu',
  'job.failure.soundless_not_evaluated.detail':
    'Intet blev designet. Vælg i stedet en playliste, eller prøv igen senere.',
  'job.failure.music_unlinked.title': 'Musikken i denne version findes ikke i din MOWL-konto',
  'job.failure.music_unlinked.detail':
    'Musikken i denne version findes ikke i din tilsluttede MOWL-konto. Ændr den, og gem den nye version.',
  'job.failure.soundlessInvalid.title': 'Anmodningen kunne ikke bruges',
  'job.failure.soundlessInvalid.detail':
    'Et hold uden musik er til cykling og har hverken sange eller mix. Tjek formularen, og start forfra.',
  'job.failure.fallback.title': 'Designet blev ikke færdigt',
  'job.failure.fallback.detail': 'Prøv igen senere.',
  'job.failure.featureOff.soundless.title': 'Hold uden musik er slået fra lige nu',
  'job.failure.featureOff.soundless.detail':
    'Intet blev designet. Vælg i stedet en playliste, eller prøv igen senere.',
  'job.failure.featureOff.swedebeat.title': 'Swedebeat er slået fra lige nu',
  'job.failure.featureOff.swedebeat.detail':
    'Intet blev designet. Prøv igen senere, eller brug en anden slags musik.',
  'job.failure.featureOff.running.title': 'Løbehold er slået fra lige nu',
  'job.failure.featureOff.running.detail':
    'Intet blev designet. Prøv igen senere, eller design et cykelhold.',
  'job.stage.music': 'Læser din playliste',
  'job.stage.design': 'Bygger dit hold',
  'job.stage.revise': 'Ændrer holdet',
  'job.stage.prepare': 'Tjekker holdet og din MOWL-konto',
  'job.stage.read': 'Læser træningspasset i MOWL',
  'job.stage.save': 'Skriver til MOWL',
  'job.stage.verify': 'Tjekker, hvad MOWL beholdt',
  'job.stage.fit': 'Flytter ændringerne over på de rettede beats',
  'job.stage.default': 'Går i gang',
  'job.step.pool_search': 'Finder sange, der passer',
  'job.step.pool_write': 'Gemmer din arbejdsplayliste',
  'job.step.pool_analyse': 'MOWL lytter til musikken',
  'job.step.choose': 'Vælger de bedste sange til hver del',
  'job.step.prepare': 'Tjekker dine konti',
  'job.step.refitMusic': 'Læser din musik',
  'job.retry.upstream_unavailable': 'MOWL svarede ikke. Prøver igen om lidt.',
  'job.retry.rate_limited': 'MOWL bad classbeat om at sætte tempoet ned. Prøver igen om lidt.',
  'job.retry.model_unavailable': 'Designeren har travlt. Prøver igen om lidt.',
  'job.retry.default': 'Noget gik galt. Prøver igen om lidt.',
  'job.mixStage': 'Henter og analyserer dit mix',
  'job.waitingForSlot': 'Venter på en ledig plads. Andre mix bliver analyseret før dit.',
  'job.waitingToStart': 'Venter på en ledig plads.',
  'job.youCanLeave':
    'Du kan forlade denne side. Dit hold fortsætter, og du kan vende tilbage til denne adresse eller finde holdet under Mine hold.',
  'job.use.changeSongs':
    'Denne anmodning handler kun om sangene, så intet blev ændret. Brug Skift sange for at vælge andre.',
  'job.use.newClassForSongs':
    'Denne anmodning handler kun om sangene, så intet blev ændret. Dette hold bruger din egen playliste; for at bruge anden musik skal du designe et nyt hold.',
  'job.use.otherMusicRunning':
    'Denne anmodning handler kun om musikken, så intet blev ændret. Et løbehold bruger en playliste fra din MOWL-konto og kan ikke skifte sange; for at bruge anden musik skal du designe et nyt hold.',
  'job.use.finishSongs':
    'Denne anmodning handler kun om sangene, så intet blev ændret. Gør sangvalget på denne side færdigt, eller prøv det igen først, og brug derefter Skift sange.',
  'job.use.newClassForMusicSoundless':
    'Denne anmodning handler kun om musikken, så intet blev ændret. Dette hold har ingen musik; for at tilføje musik skal du designe et nyt hold med en playliste.',
  'job.revise.upstream_unavailable': 'MOWL kunne ikke nås. Intet blev ændret. Prøv igen om nogle minutter.',
  'job.revise.rate_limited':
    'MOWL bad classbeat om at sætte tempoet ned. Intet blev ændret. Vent nogle minutter, og prøv igen.',
  'job.revise.model_unavailable': 'Designeren har travlt. Intet blev ændret. Prøv igen om nogle minutter.',
  'job.revise.lease_lost': 'Ændringen blev afbrudt for mange gange. Intet blev ændret. Prøv igen.',
  'job.revise.operation_busy':
    'Et andet design fra dig brugte stadig den samme playliste. Intet blev ændret. Prøv igen om lidt.',
  'job.revise.not_connected':
    'MOWL er ikke tilsluttet. Tilslut det på din kontoside, og spørg derefter igen.',
  'job.revise.needs_reconnect':
    'MOWL accepterer ikke længere det gemte login. Tilslut det igen på din kontoside, og spørg derefter igen.',
  'job.revise.key_unavailable': 'Det gemte MOWL-login kunne ikke læses. Tilslut MOWL igen på din kontoside.',
  'job.revise.invalid_input': 'Den anmodning kunne ikke bruges. Intet blev ændret. Formulér dig anderledes.',
  'job.revise.account_disabled':
    'Din konto kan ikke ændre hold lige nu. Kontakt personen, der inviterede dig.',
  'job.revise.model_refused':
    'Designeren afviste denne anmodning. Intet blev ændret. Formulér dig anderledes.',
  'job.revise.model_config':
    'classbeat kan ikke ændre hold lige nu. Det er et problem hos os. Prøv igen senere.',
  'job.revise.invalid_output':
    'Designeren lavede ikke en brugbar ændring eller en, der ikke ændrer noget. Intet blev ændret. Prøv igen, eller formulér dig anderledes.',
  'job.revise.limit_exceeded':
    'Denne anmodning krævede mere arbejde, end en ændring må bruge. Intet blev ændret. Bed om noget mindre.',
  'job.revise.outbound_blocked': 'Noget gik galt hos os. Intet blev ændret. Prøv igen senere.',
  'job.revise.music_unlinked':
    'Musikken i denne version findes ikke i din tilsluttede MOWL-konto. Ændr den, og gem den nye version.',
  'job.revise.soundless_not_evaluated':
    'Hold uden musik er ikke klar til din plan endnu. Intet blev ændret. Prøv igen senere.',
  'job.revise.program_changed': 'Dette træningspas blev ændret i MOWL, efter at classbeat gemte det.',
  'job.revise.program_missing': 'Dette træningspas findes ikke længere i din MOWL-konto.',
  'job.revise.program_other_sport':
    'Dette træningspas i MOWL er til en anden sport end dette hold, så classbeat erstattede det ikke.',
  'job.revise.soundlessFeatureOff':
    'Hold uden musik er slået fra lige nu. Intet blev ændret. Prøv igen senere.',
  'job.revise.default': 'Ændringen blev ikke færdig. Intet blev ændret. Prøv igen senere.',
  'songs.stage.pool_search': 'Finder sange, der kunne passe',
  'songs.stage.pool_write': 'Skriver din arbejdsplayliste i Spotify',
  'songs.stage.pool_analyse': 'Venter på, at MOWL analyserer sangene',
  'songs.stage.choose': 'Vælger sangene til dit hold',
  'songs.stage.create': 'Opretter din playliste i Spotify',
  'songs.stage.fill': 'Tilføjer de godkendte sange til din playliste',
  'songs.stage.attach': 'Føjer din playliste til din MOWL-musik',
  'songs.approve.expired': 'Sangene udløb, før de blev godkendt. Intet blev skrevet til din Spotify.',
  'songs.approve.superseded': 'En nyere liste med sange har afløst disse, så de ikke kan godkendes mere.',
  'songs.approve.hash_mismatch':
    'Sangene blev ændret, efter at siden blev indlæst, så intet blev godkendt. Genindlæs siden for at se den aktuelle liste.',
  'songs.approve.not_connected':
    'Spotify eller MOWL er ikke tilsluttet. Tilslut det på din kontoside, kom så tilbage, og godkend.',
  'songs.approve.account_changed':
    'Din Spotify-konto er ikke den, som sangene blev valgt til. Bed om nye sange.',
  'songs.approve.not_waiting': 'Disse sange kan ikke godkendes mere. De er allerede godkendt eller stoppet.',
  'songs.approve.busy': 'Du har allerede designs i gang. Vent, til et er færdigt, og godkend derefter.',
  'songs.approve.quota': 'Du har brugt det, din plan tillader for nu.',
  'songs.approve.not_found': 'Sangene blev ikke fundet, eller også er de ikke dine.',
  'songs.approve.invalid_input':
    'Godkendelsen blev ikke accepteret. Genindlæs siden for at se de aktuelle sange.',
  'songs.approve.default': 'Det virkede ikke. Prøv igen.',
  'songs.start.song_picking_off': 'Valg af sange er ikke tilgængeligt lige nu.',
  'songs.start.consent_missing':
    'Tillad først arbejdsplaylisten i dine Spotify-indstillinger på din kontoside.',
  'songs.start.pool_busy': 'Et andet sangvalg er stadig i gang. Vent, til det er færdigt, og prøv så igen.',
  'songs.start.class_busy':
    'Dette hold har allerede en opgave i gang. Vent, til den er færdig, og prøv så igen.',
  'songs.start.stale_base': 'Holdet blev ændret i mellemtiden. Genindlæs siden, og prøv igen.',
  'songs.start.revision_limit': 'Dette hold har nået det højeste antal versioner, det kan have.',
  'songs.start.chat_full': 'Samtalen for dette hold er fuld, så det kan ikke ændres mere.',
  'songs.start.not_song_picked':
    'Musikken til dette hold blev ikke valgt af Claude, så dets sange kan ikke ændres her.',
  'songs.start.invalid_input': 'Anmodningen blev ikke accepteret. Tjek felterne, og prøv igen.',
  'songs.start.not_connected': 'Spotify eller MOWL er ikke tilsluttet. Tilslut det først på din kontoside.',
  'songs.start.receipt_invalid':
    'Disse sange kan ikke gøres til en playliste igen: de er allerede brugt eller aldrig godkendt. Vælg sange igen.',
  'songs.start.account_changed':
    'Din Spotify-konto er ikke den, som sangene blev valgt til. Vælg sange igen.',
  'songs.start.write_unresolved':
    'Et tidligere trin hos Spotify er stadig uafklaret. Afgør det først (Tjek igen eller Gør det alligevel), og prøv så igen.',
  'songs.start.idempotency_mismatch': 'Det virkede ikke. Tjek formularen, og prøv igen.',
  'songs.start.default': 'Det virkede ikke. Prøv igen.',
  'songs.failure.invalid_grant.title': 'Spotify accepterer ikke længere forbindelsen',
  'songs.failure.app_misconfigured.title': 'Spotify genkender ikke denne appopsætning',
  'songs.failure.not_allowlisted.title': 'Spotify afviste denne konto',
  'songs.failure.forbidden.title': 'Spotify afviste anmodningen',
  'songs.failure.rate_limited.title': 'Spotify eller MOWL bad classbeat om at sætte tempoet ned',
  'songs.failure.rate_limited.detail': 'Vent nogle minutter, og prøv igen.',
  'songs.failure.quota_exhausted.title': 'Din Spotify-app har brugt sin kvote op',
  'songs.failure.quota_exhausted.detail':
    'Din Spotify-app har brugt sin kvote op for nu. Prøv igen om nogle timer.',
  'songs.failure.upstream_unavailable.title': 'Spotify eller MOWL kunne ikke nås',
  'songs.failure.upstream_unavailable.detail': 'classbeat prøvede flere gange. Prøv igen om nogle minutter.',
  'songs.failure.not_found.title': 'En playliste findes ikke længere i Spotify',
  'songs.failure.not_found.detail': 'Arbejdsplaylisten findes ikke længere i Spotify. Start forfra.',
  'songs.failure.scope_mismatch.title': 'Spotify gav ikke den adgang, som classbeat har brug for',
  'songs.failure.account_changed.title': 'Det er ikke den Spotify-konto, som sangene blev valgt til',
  'songs.failure.access_denied.title': 'Spotify blev ikke tilsluttet',
  'songs.failure.not_connected.title': 'Spotify eller MOWL er ikke tilsluttet',
  'songs.failure.not_connected.detail': 'Tilslut det på din kontoside, og start derefter forfra.',
  'songs.failure.consent_missing.title': 'Arbejdsplaylisten er ikke tilladt endnu',
  'songs.failure.pool_unusable.title': 'Din arbejdsplayliste blev ændret',
  'songs.failure.pool_missing.title': 'Din arbejdsplayliste fandtes ikke længere i Spotify',
  'songs.failure.pool_missing.detail': 'Din arbejdsplayliste fandtes ikke længere i Spotify. Prøv igen.',
  'songs.failure.pool_not_synced.title': 'MOWL er ikke nået med endnu',
  'songs.failure.playlist_mismatch.title': 'Spotifys playliste stemte ikke med de godkendte sange',
  'songs.failure.playlist_mismatch.detail':
    'Spotifys playliste stemte ikke med de godkendte sange; intet blev designet. Opret den igen.',
  'songs.failure.pool_too_small.title': 'Der blev ikke fundet nok brugbare sange',
  'songs.uncertain.title': 'classbeat kunne ikke afgøre, om din playliste blev skrevet i Spotify',
  'songs.uncertain.text1':
    'Spotify svarede ikke i tide, mens classbeat skrev playlisten til dine godkendte sange. Playlisten findes måske i din Spotify nu, eller også gør den ikke.',
  'songs.uncertain.text2':
    'Tjek igen spørger Spotify, hvad der er der. Gør det alligevel skriver uden at vide det.',
  'songs.uncertain.overwriteWarning':
    'Hvis playlisten allerede findes i din Spotify, kan du ende med to playlister med samme navn. Du kan slette den ekstra i Spotify. Gå kun videre, hvis du er sikker på, at den ikke blev skrevet.',
  'songs.outcome.found':
    'Playlisten findes i din Spotify. Opret playlisten igen for at komme videre med dit hold.',
  'songs.outcome.nothing':
    'Intet blev skrevet til Spotify. Opret playlisten igen for at komme videre med dit hold.',
  'songs.outcome.decided':
    'Du valgte at skrive den igen. Opret playlisten igen for at komme videre med dit hold.',
  'songs.outcome.still_unsure': 'Spotify giver stadig ikke besked. Vent nogle minutter, og tjek igen.',
  'common.didNotWork': 'Det virkede ikke. Prøv igen.',
  'songs.uncertain.mayStillRunning':
    'classbeat skriver måske stadig til Spotify, så det kan ikke afgøres endnu. Vent nogle minutter.',
  'songs.uncertain.alreadySettled': 'Det var allerede afgjort. Genindlæs siden.',
  'songs.uncertain.waiting':
    'classbeat sender måske stadig dette til Spotify, så Tjek igen og Gør det alligevel venter nogle minutter.',
  'songs.uncertain.confirm': 'Bekræft',
  'songs.uncertain.confirmYes': 'Ja, den blev ikke skrevet',
  'songs.uncertain.checkAgain': 'Tjek igen',
  'songs.uncertain.doAnyway': 'Gør det alligevel',
  'job.steps.label': 'Trin',
  'job.steps.done': ' (færdig)',
  'job.notFound.title': 'Holdet blev ikke fundet',
  'job.notFound.text': 'Dette design findes ikke, eller også er det ikke dit.',
  'job.notFound.designLink': 'Design et hold',
  'job.signedOut.title': 'Du er logget ud',
  'job.signedOut.text': 'Log ind igen for at se dette design. Det fortsætter imens.',
  'job.signedOut.signIn': 'Log ind',
  'job.moment.title': 'Et øjeblik',
  'job.moment.unreachable': 'Serveren kunne ikke nås. Prøver igen.',
  'job.moment.loading': 'Indlæser',
  'job.dropped': 'Forbindelsen til serveren blev afbrudt. Prøver igen.',
  'job.cancelled.title': 'Du stoppede dette design',
  'job.cancelled.detail': 'Intet blev gemt.',
  'job.expired.title': 'Dette design udløb',
  'job.expired.detail': 'Start et nyt.',
  'job.waitingForYou.title': 'Venter på dig',
  'job.waitingForYou.text': 'Dette design venter på et svar fra dig.',
  'job.cancelError.finished': 'Dette design er lige blevet færdigt.',
  'job.yourClass': 'dit hold',
  'job.findingSongs': 'Finder sange til {name}…',
  'job.designing': 'Designer {name}…',
  'job.stepOf': 'Trin {step} af {total}',
  'job.stopping': 'Stopper. Det tager et øjeblik.',
  'job.addingPlaylist': 'Føjer din playliste til din MOWL-musik.',
  'job.toolCalls': 'Designeren har tjekket sit udkast {count} gang(e).',
  'job.stop': 'Stop designet',
  'job.ready.title': 'Dit hold er klar',
  'job.ready.loadingDraft': 'Indlæser udkastet',
  'job.ready.noDraft': 'Dette design blev færdigt uden et udkast.',
  'job.ready.draftFailed': 'Udkastet kunne ikke indlæses.',
  'job.ready.openClass': 'Åbn holdet',
  'job.ready.designAnother': 'Design et hold mere',
  'job.again.notConnected':
    'MOWL er ikke tilsluttet, eller det blev tilsluttet igen, siden dette design startede. Tilslut det på din kontoside, hvis det er nødvendigt, og start derefter et nyt design.',
  'job.again.classBusy':
    'Dette hold har allerede et design i gang, måske fra en anden fane. Vent, til det er færdigt, og prøv så igen.',
  'job.again.tooMany': 'Du har allerede designs i gang. Vent, til et er færdigt.',
  'job.again.design': 'Design igen',
  'job.again.startNew': 'Start et nyt design',
  'job.again.tryAgain': 'Prøv igen',
  'job.matchStopped': 'Swedebeat-matchningen stoppede',
  'job.goToAccount': 'Gå til din konto',
  'songs.list.label': 'Sange',
  'songs.list.openSpotify': 'ÅBN SPOTIFY',
  'songs.list.openSpotifyLabel': 'ÅBN SPOTIFY, {title}',
  'songs.list.bpm': '{bpm} BPM',
  'songs.list.new': 'Ny',
  'songs.list.replace': 'Erstat',
  'songs.list.replaceLabel': 'Erstat {title}',
  'music.label': 'Musik',
  'music.playlist': 'Brug min playliste',
  'music.pick': 'Vælg sange til mig',
  'music.swedebeat': 'Brug mit Swedebeat-mix',
  'music.none': 'Ingen musik',
  'music.needsSpotify': 'Valg af sange kræver Spotify. {link} først.',
  'music.connectSpotify': 'Tilslut Spotify',
  'music.needsConsent': 'Tillad først arbejdsplaylisten i dine Spotify-indstillinger. {link}',
  'music.openSettings': 'Åbn Spotify-indstillingerne',
  'music.pickHint':
    'Claude foreslår sange, tjekker dem mod MOWLs BPM og længde og viser dig listen. Kandidaterne havner i en privat arbejdsplayliste i din Spotify; playlisten til dit hold oprettes først, når du har godkendt sangene.',
  'music.noneHint':
    'classbeat designer holdet uden playliste eller sange: du får intervallerne, anstrengelserne og instruktionerne, og du spiller din egen musik. Det gemmes i MOWL uden musik.',
  'music.swedebeatHint':
    'classbeat designer holdet efter sangene og beatsene i dit mix. Du behøver ikke et Swedebeat-login i classbeat til dette; MOWL viser mixene i din Intelligent Cycling-konto.',
  'songs.change.writeRequest': 'Skriv, hvad du vil have anderledes ved sangene.',
  'songs.change.tooLong': 'Anmodningen må højst være {max} tegn.',
  'songs.change.markSongs': 'Markér de sange, der skal erstattes, eller erstat dem alle.',
  'songs.change.notConnected': 'Spotify eller MOWL er ikke tilsluttet. Tilslut det først på din kontoside.',
  'songs.change.title': 'Skift sange',
  'songs.change.wait': 'Vent først, til den igangværende opgave er færdig.',
  'songs.change.loadFailed': 'Sangene kunne ikke indlæses. Genindlæs siden.',
  'songs.change.loading': 'Indlæser sangene',
  'songs.change.intro':
    'Markér de sange, du vil have erstattet. De andre sange bliver, og du godkender den nye liste, før noget bliver skrevet til din Spotify.',
  'songs.change.different': 'Hvad skal være anderledes?',
  'songs.change.marked': 'Skift de markerede sange',
  'songs.change.all': 'Skift alle sange',
  'songs.ended.stoppedTitle': 'Du stoppede dette',
  'songs.ended.stoppedApproved':
    'Du stoppede jobbet, efter at du havde godkendt sangene. Holdet blev ikke færdigt.',
  'songs.ended.stoppedNothing': 'Intet blev skrevet til din Spotify.',
  'songs.ended.expiredApproved': 'Dette job udløb',
  'songs.ended.expiredSongs': 'Disse sange udløb',
  'songs.ended.expiredApprovedDetail': 'Jobbet tog for lang tid og blev afsluttet.',
  'songs.ended.expiredSongsDetail': 'Ingen godkendte sangene i tide. Intet blev skrevet til din Spotify.',
  'songs.ended.loadFailed': 'Det, der mangler at blive afgjort, kunne ikke indlæses. Genindlæs siden.',
  'songs.ended.checking': 'Tjekker, hvad der skete',
  'songs.ended.nothingLeft': 'Der er intet tilbage at afgøre. Du kan oprette playlisten igen.',
  'songs.ended.reconnect': 'Tilslut Spotify igen',
  'songs.ended.createAgain': 'Opret playlisten igen',
  'songs.ended.pickAgain': 'Vælg sange igen',
  'songs.ended.startNew': 'Start et nyt hold',
  'songs.ended.backToClass': 'Tilbage til holdet',
  'songs.approval.privacy':
    'Når du godkender, opretter classbeat en privat playliste med disse sange i din egen Spotify-konto. En privat playliste vises ikke på din profil eller i søgningen, men alle med linket til den kan åbne den.',
  'songs.approval.stopped':
    'Disse sange blev stoppet, men classbeat kunne ikke læse holdet, så intet nyt blev startet. Tryk på Bed om ændringer igen for at prøve en gang til.',
  'songs.approval.cannotChange': 'Disse sange kan ikke ændres mere, så intet blev startet. Vælg sange igen.',
  'songs.approval.loading': 'Indlæser sangene',
  'songs.approval.loadFailedTitle': 'Sangene kunne ikke indlæses',
  'songs.approval.loadFailed': 'Sangene kunne ikke indlæses. Intet blev godkendt.',
  'songs.approval.approvedTitle': 'Du godkendte disse sange',
  'songs.approval.approvedText': 'classbeat laver din playliste og gør holdet færdigt.',
  'songs.approval.refresh': 'Opdater',
  'songs.approval.supersededTitle': 'En nyere liste afløste disse sange',
  'songs.approval.supersededText':
    'En nyere liste med sange har afløst disse, så de ikke kan godkendes mere.',
  'songs.approval.expiredTitle': 'Disse sange udløb',
  'songs.approval.stoppedTitle': 'Disse sange blev stoppet',
  'songs.approval.expiredText': 'Sangene udløb, før de blev godkendt. Intet blev skrevet til din Spotify.',
  'songs.approval.stoppedText': 'Sangene blev stoppet. Intet blev skrevet til din Spotify.',
  'songs.approval.eyebrow': 'Playliste til godkendelse',
  'songs.approval.heading': 'Sange til dit hold',
  'songs.approval.summary': '{count} sange, {total} i alt. Du bad om {target}.',
  'songs.approval.omittedNoBpm': {
    one: '{count} sang blev udeladt, fordi MOWL ikke har nogen BPM for den.',
    other: '{count} sange blev udeladt, fordi MOWL ikke har nogen BPM for dem.',
  },
  'songs.approval.expiry':
    'Godkend senest {when}. Derefter udløber sangene, og intet bliver skrevet til din Spotify.',
  'songs.approval.approve': 'Godkend sangene',
  'songs.approval.noteLabel': 'Hvad skal være anderledes? (valgfrit)',
  'songs.approval.markHint':
    'Markér de sange, der skal erstattes, og bed derefter om ændringer. De andre sange bliver.',
  'songs.approval.askChanges': 'Bed om ændringer',
  'songs.approval.stopAndChange': 'Stop og ændr anmodningen',
  'songs.approval.reload': 'Genindlæs siden',
  'songs.approval.openClass': 'Åbn holdet',

  // Home, setup checklist, theme, dialog, unsubscribe page (ML5d).
  'home.welcome': 'Velkommen tilbage',
  'home.welcomeName': 'Velkommen tilbage, {name}',
  'home.design.title': 'Design et nyt hold',
  'home.design.text': 'Beskriv det, så vælger classbeat sangene og afstemmer indsatserne efter musikken.',
  'home.design.presets': 'Start fra en forudindstilling',
  'home.design.button': 'Design et hold',
  'home.recent': 'Seneste hold',
  'home.loading': 'Indlæser dine hold',
  'home.loadFailed': 'Dine hold kunne ikke indlæses.',
  'home.empty': 'Ingen hold endnu. Dit første vises her.',
  'home.seeAll': 'Se alle dine hold',
  'setup.title': 'Kom i gang',
  'setup.account': 'Opret din konto',
  'setup.mowl': 'Tilslut MOWL',
  'setup.mowlAgain': 'Tilslut MOWL igen',
  'setup.spotify': 'Tilslut Spotify (valgfrit)',
  'setup.spotifyAgain': 'Tilslut Spotify igen (valgfrit)',
  'setup.action': 'Tilslut',
  'setup.done': ', færdig',
  'setup.notDone': ', ikke færdig endnu',
  'theme.label': 'Tema',
  'theme.dark': 'Mørkt',
  'theme.light': 'Lyst',
  'theme.system': 'Samme som enheden',
  'dialog.close': 'Luk',
  'spotifyPage.back': 'Tilbage til din konto',
  'unsub.title': 'Afmeld',
  'unsub.needsLink': 'Denne side kræver linket fra mailen.',
  'unsub.doneTitle': 'Du er afmeldt',
  'unsub.doneAll': 'classbeat sender ikke længere valgfri mail til dig.',
  'unsub.doneCategory': 'classbeat sender ikke længere denne slags mail til dig.',
  'unsub.doneNote':
    'Links til login, nulstilling af adgangskode og bekræftelse sendes altid. Du kan ændre, hvilken valgfri mail du får, herunder sikkerhedsbeskeder, under Mail på din kontoside.',
  'unsub.linkInvalid':
    'Dette link er ikke længere gyldigt. Brug linket i en nyere mail eller afsnittet Mail i din konto.',
  'unsub.intro': 'Vælg, hvad der skal stoppes. Intet ændres, før du trykker på en knap.',
  'unsub.stopCategory': 'Stop denne slags mail',
  'unsub.stopAll': 'Stop al valgfri mail',

  // Music page.
  'music.page.title': 'Musik',
  'music.page.connectTitle': 'Tilslut Spotify',
  'music.page.connectText':
    'Tilslut Spotify, så classbeat kan vælge sange til dig og lave playlister til dine hold.',
  'music.page.connectButton': 'Tilslut Spotify',
  'music.page.madeTitle': 'Playlister, classbeat har lavet',
  'music.page.madeLoading': 'Indlæser dine playlister',
  'music.page.madeFailed': 'Dine playlister kunne ikke indlæses.',
  'music.page.madeEmpty':
    'Ingen playlister endnu. Når du godkender sange til et hold, vises dets playliste her.',
  'music.page.mowlTitle': 'Dine playlister i MOWL',
  'music.page.mowlLoading': 'Indlæser din MOWL-musik',
  'music.page.mowlFailed':
    'Dine MOWL-playlister kunne ikke indlæses. Tilslut MOWL på {link}, eller prøv igen om nogle minutter.',
  'music.page.mowlFailedLink': 'din kontoside',
  'music.page.mowlEmpty': 'MOWL viser ingen Spotify-playlister for din konto.',
  'music.page.untitled': 'Unavngiven playliste',
  'music.page.forClass': 'Til {link}',
  'music.page.openSpotify': 'ÅBN SPOTIFY',

  // Plan refusals (api/planMessages.ts) and month names.
  'month.long.1': 'januar',
  'month.long.2': 'februar',
  'month.long.3': 'marts',
  'month.long.4': 'april',
  'month.long.5': 'maj',
  'month.long.6': 'juni',
  'month.long.7': 'juli',
  'month.long.8': 'august',
  'month.long.9': 'september',
  'month.long.10': 'oktober',
  'month.long.11': 'november',
  'month.long.12': 'december',
  'plan.dayMonth': '{day}. {month}',
  'plan.thisMonth': 'denne måned',
  'plan.classes': { one: '{count} hold', other: '{count} hold' },
  'plan.designs': { one: '{count} design', other: '{count} designs' },
  'plan.feature.games': 'spil',
  'plan.feature.races': 'race',
  'plan.feature.ftp_tests': 'FTP-test',
  'plan.saveWays': 'Ændr det, så det passer, eller gør en tidligere version aktuel, og gem derefter igen.',
  'plan.quota.length.save':
    'Din plan tillader ikke et så langt hold, så denne version kan ikke gemmes. {ways}',
  'plan.quota.length.saveLimit':
    'Din plan tillader hold på op til {limit} minutter, og denne version er længere, så den kan ikke gemmes. {ways}',
  'plan.quota.length.version': 'Den version er længere, end din plan tillader. Vælg en anden version.',
  'plan.quota.length.versionLimit':
    'Din plan tillader hold på op til {limit} minutter, og den version er længere. Vælg en anden version.',
  'plan.quota.length.new': 'Din plan tillader ikke et så langt hold. Vælg en kortere længde.',
  'plan.quota.length.newLimit': 'Din plan tillader hold på op til {limit} minutter. Vælg en kortere længde.',
  'plan.quota.feature.save':
    'Denne version bruger noget, som din plan ikke omfatter, så den kan ikke gemmes. {ways}',
  'plan.quota.feature.saveNamed':
    'Denne version bruger {name}, som din plan ikke omfatter, så den kan ikke gemmes. {ways}',
  'plan.quota.feature.version':
    'Den version bruger noget, som din plan ikke omfatter. Vælg en anden version.',
  'plan.quota.feature.versionNamed':
    'Den version bruger {name}, som din plan ikke omfatter. Vælg en anden version.',
  'plan.quota.feature.new': 'Din plan omfatter ikke en del af dette hold. Tag den del ud, og prøv igen.',
  'plan.quota.feature.newNamed': 'Din plan omfatter ikke {name}. Tag det ud, og prøv igen.',
  'plan.quota.saves.window.day': 'de seneste 24 timer',
  'plan.quota.saves.window.hour': 'den seneste time',
  'plan.quota.saves.later.day': 'Prøv igen i morgen.',
  'plan.quota.saves.later.hour': 'Prøv igen om en time.',
  'plan.quota.saves':
    'Du har gemt i MOWL lige så mange gange, som classbeat tillader inden for {window}. {later}',
  'plan.quota.savesLimit':
    'Du har gemt eller opdateret {classes} i MOWL inden for {window}, hvilket er så mange, som classbeat tillader. {later}',
  'plan.quota.moreFrom': 'Flere fra {day}.',
  'plan.quota.moreNextMonth': 'Flere i begyndelsen af næste måned.',
  'plan.quota.cost':
    'Du har brugt det, din plan tillader for {month}, så der kan ikke designes flere hold lige nu. {more}',
  'plan.quota.classesNoLimit': 'Du har brugt dine hold for {month}. {more}',
  'plan.quota.classesUsed': 'Du har brugt {used} af dine {classes} for {month}. {more}',
  'plan.quota.classesAll': 'Du har brugt dine {classes} for {month}. {more}',
  'plan.busy.generic': 'Du har allerede designs i gang. Vent, til et er færdigt, og prøv så igen.',
  'plan.busy.limit':
    'Din plan tillader {designs} ad gangen, og så mange kører. Vent, til et er færdigt, og prøv så igen.',
  'plan.reason.code_invalid':
    'Koden er ikke gyldig. Tjek den, og prøv igen. Den kan være udløbet eller brugt op.',
  'plan.reason.already_redeemed': 'Du har allerede brugt den kode.',
  'plan.reason.trial_used': 'Du har allerede haft din prøveperiode.',
  'plan.reason.trial_off': 'Prøveperioder er ikke tilgængelige lige nu.',
  'plan.source.grant': 'tildelt dig',
  'plan.source.code': 'fra en kode',
  'plan.source.trial': 'din prøveperiode',
  'plan.source.subscription': 'et abonnement',

  // Account: plan and usage card.
  'plan.title': 'Plan og forbrug',
  'plan.classesLine': { one: '{held} af {count} hold i {month}', other: '{held} af {count} hold i {month}' },
  'plan.redeemed': 'Koden er indløst.',
  'plan.trialStarted': 'Din prøveperiode er startet.',
  'plan.loadFailed': 'Din plan kunne ikke indlæses.',
  'plan.line': 'Din plan: {name}',
  'plan.lineSource': 'Din plan: {name} ({source})',
  'plan.lineEnd': 'Din plan: {name}, til {end}',
  'plan.lineSourceEnd': 'Din plan: {name} ({source}), til {end}',
  'plan.budgetLabel': 'Andel af månedens budget, der er brugt',
  'plan.budgetText': '{percent} % af månedens budget er brugt. Den starter forfra {day}.',
  'plan.firstFallback': 'den 1.',
  'plan.redeemLabel': 'Indløs en kode',
  'plan.redeem': 'Indløs',
  'plan.trial': 'Start din prøveperiode på {days} dage',

  // Account: usage card.
  'usage.title': 'Forbrug',
  'usage.note.estimate_at_list_prices':
    'Prisen er et estimat til udbyderens listepriser. Det er ikke en faktura.',
  'usage.note.sdk_retries':
    'Softwaren, der taler med udbyderen, prøver stille igen inden for en anmodning. Disse ekstra forsøg tælles ikke med.',
  'usage.note.lost_lease_turn':
    'Et trin, der blev færdigt, efter at classbeat allerede havde givet sit arbejde videre til en anden arbejdsproces, kan ikke registreres.',
  'usage.note.failed_stream_turn':
    'En anmodning, der fejlede, efter at udbyderen var begyndt at fakturere den, returnerer intet forbrug og tælles derfor ikke med.',
  'usage.note.other': 'Der er en anden grund til, at dette tal kan afvige fra udbyderens faktura.',
  'usage.kind.design_draft': 'Designer et hold',
  'usage.kind.revise_draft': 'Ændrer et hold',
  'usage.loadFailed': 'Dit forbrug kunne ikke indlæses.',
  'usage.period': 'Dit forbrug i {month} (UTC-måneder).',
  'usage.tokens': 'Tokens',
  'usage.cost': 'Anslået pris',
  'usage.designed': 'Designede hold',
  'usage.revisions': 'Revisioner',
  'usage.tokensExplained':
    'Tokens er de tekststykker, designeren læser og skriver: {input} ind, {output} ud. At gemme i MOWL bruger ingen.',
  'usage.tokensExplainedReused':
    'Tokens er de tekststykker, designeren læser og skriver: {input} ind, {output} ud og {reused} genbrugt fra tidligere anmodninger. At gemme i MOWL bruger ingen.',
  'usage.byKind': 'Efter slags arbejde og model',
  'usage.col.work': 'Arbejde',
  'usage.col.model': 'Model',
  'usage.col.tokens': 'Tokens',
  'usage.col.cost': 'Pris',
  'usage.leavesOut': 'Hvad dette ikke omfatter',
  'usage.lowerThanBill': 'Tallet kan være lidt lavere end udbyderens faktura.',

  // Account: MOWL connection card.
  'mowl.err.invalid_credentials': 'MOWL accepterede ikke den e-mailadresse og adgangskode.',
  'mowl.err.upstream_unavailable': 'MOWL kunne ikke nås. Prøv igen om nogle minutter.',
  'mowl.err.unknown': 'MOWL svarede på en måde, classbeat ikke havde forventet. Prøv igen senere.',
  'mowl.last.needs_reconnect': 'MOWL accepterer ikke længere det gemte login. Tilslut igen.',
  'mowl.last.decrypt_failed': 'Det gemte login kunne ikke læses. Tilslut igen.',
  'mowl.last.rate_limited': 'MOWL bad classbeat om at sætte tempoet ned.',
  'mowl.last.upstream_unavailable': 'MOWL kunne ikke nås sidste gang.',
  'mowl.last.other': 'Den seneste brug af denne forbindelse mislykkedes.',
  'mowl.connected': 'MOWL er tilsluttet.',
  'mowl.disconnected': 'MOWL er afbrudt. classbeat bruger ikke længere dets login.',
  'mowl.title': 'MOWL (Intelligent Cycling)',
  'mowl.loadFailed': 'Forbindelserne kunne ikke indlæses.',
  'mowl.status': 'Status',
  'mowl.statusConnected': 'Tilsluttet',
  'mowl.statusAgain': 'Skal tilsluttes igen',
  'mowl.since': 'Tilsluttet siden',
  'mowl.lastProblem': 'Seneste problem',
  'mowl.reconnectWarning': 'Hvis du tilslutter igen, erstattes det gemte login. {warning}',
  'mowl.signInText':
    'Log ind med din Intelligent Cycling-konto (MOWL). classbeat bruger din adgangskode én gang for at hente et login-token og gemmer den ikke.',
  'mowl.emailLabel': 'MOWL-mail',
  'mowl.passwordLabel': 'MOWL-adgangskode',
  'mowl.connectButton': 'Tilslut MOWL',
  'mowl.connectAgainButton': 'Tilslut MOWL igen',
  'mowl.disconnectText':
    'Når du afbryder forbindelsen, slettes det token, classbeat har. MOWL giver ikke classbeat mulighed for at afslutte login hos sig; skift din MOWL-adgangskode for at afslutte det dér.',
  'mowl.confirmDisconnect': 'Bekræft afbrydelse',
  'mowl.yesDisconnect': 'Ja, afbryd MOWL',
  'mowl.keep': 'Behold forbindelsen',
  'mowl.disconnect': 'Afbryd MOWL',

  // Account: Spotify working-playlist consent switch.
  'consent.revokeText':
    'Hvis du trækker samtykket tilbage, glemmer classbeat arbejdsplaylisten og holder op med at bruge den. Playlisten bliver i din Spotify, privat, med de seneste sange; slet den dér, hvis du ikke vil have den. Valg af sange kræver dit samtykke igen, og næste valg opretter en ny arbejdsplayliste.',
  'consent.given': 'Tak. classbeat må bruge arbejdsplaylisten.',
  'consent.withdrawn': 'Dit samtykke er trukket tilbage. Playlisten bliver i din Spotify.',
  'consent.label': 'Tillad arbejdsplaylisten',
  'consent.stateOn': 'Du har tilladt arbejdsplaylisten.',
  'consent.stateOff': 'Du har ikke tilladt den endnu.',
  'consent.confirmRevoke': 'Bekræft tilbagetrækning',
  'consent.yesRevoke': 'Ja, træk mit samtykke tilbage',
  'consent.keep': 'Behold mit samtykke',

  // Account: email address, password, delete, devices, email preferences, export.
  'cred.email.title': 'E-mailadresse',
  'cred.email.current': 'Din adresse er {email}.',
  'cred.email.sent':
    'Hvis {email} kan bruges, er et bekræftelseslink på vej dertil. Åbn det, mens du er logget ind, for at gøre det færdigt. Din adresse ændres først da.',
  'cred.email.newLabel': 'Ny e-mailadresse',
  'cred.email.change': 'Skift mail',
  'cred.password.title': 'Adgangskode',
  'cred.password.saved': 'Adgangskoden er gemt. Dine andre enheder blev logget ud.',
  'cred.password.mismatch': 'De to adgangskoder er ikke ens.',
  'cred.password.newLabel': 'Ny adgangskode',
  'cred.password.hint': '12 til 128 tegn.',
  'cred.password.repeatLabel': 'Gentag den nye adgangskode',
  'cred.password.save': 'Gem adgangskoden',
  'delete.button': 'Slet min konto',
  'delete.title': 'Slet din konto?',
  'delete.text':
    'Dette sletter din konto, dine hold og sanglister, din plan og dit forbrug, dine forbindelser og din login-historik. Job, der kører, stoppes først. Det kan ikke fortrydes; download dine data først, hvis du vil have en kopi. Træningspas, der er gemt i MOWL, og playlister i din Spotify bliver dér. Skriv din e-mailadresse for at bekræfte.',
  'delete.emailLabel': 'Din e-mailadresse',
  'delete.confirm': 'Slet for altid',
  'delete.keep': 'Behold min konto',
  'devices.title': 'Enheder, der er logget ind',
  'devices.loadFailed': 'Enhederne kunne ikke indlæses. Genindlæs siden, og prøv igen.',
  'devices.unknownBrowser': 'Ukendt browser',
  'devices.lastUsed': 'Sidst brugt {when}',
  'devices.movedCurrent':
    'Brugt fra et nyt netværk eller en ny browser {when}. Hvis det ikke var dig, har nogen måske en kopi af denne session: brug Log ud overalt nedenfor, og skift adgangskode.',
  'devices.movedOther':
    'Brugt fra et nyt netværk eller en ny browser {when}. Hvis det ikke var dig, så log denne enhed ud, og skift adgangskode.',
  'devices.newLocation': 'Nyt sted',
  'devices.thisDevice': 'Denne enhed',
  'devices.signOutThis': 'Log denne enhed ud',
  'devices.signOutAll': 'Log ud overalt',
  'activity.title': 'Sikkerhedshændelser',
  'activity.intro':
    'Logins, logouts og ændringer af, hvordan du logger ind, fra de seneste 30 dage. Hvis du ser noget, du ikke selv har gjort, så log ud overalt, og skift din adgangskode.',
  'activity.loadFailed': 'Sikkerhedshændelserne kunne ikke indlæses. Genindlæs siden, og prøv igen.',
  'activity.empty': 'Intet i de seneste 30 dage.',
  'activity.more': 'Vis ældre',
  'activity.from': 'Fra {ip}',
  'activity.kind.sign_in': 'Loggede ind',
  'activity.kind.sign_in.provider': 'Loggede ind med {provider}',
  'activity.kind.sign_in_failed': 'Mislykket loginforsøg',
  'activity.kind.sign_out': 'Loggede ud',
  'activity.kind.sign_out.other': 'En enhed blev logget ud fra en anden enhed',
  'activity.kind.sign_out_all': 'Loggede ud overalt',
  'activity.kind.reauth': 'Bekræftede, at det var dig',
  'activity.kind.reauth.provider': 'Bekræftede, at det var dig, med {provider}',
  'activity.kind.reauth_failed': 'Mislykket forsøg på at bekræfte, at det var dig',
  'activity.kind.reauth_failed.provider': 'Mislykket forsøg på at bekræfte, at det var dig, med {provider}',
  'activity.kind.signup_confirmed': 'Kontoen blev oprettet',
  'activity.kind.password_changed': 'Adgangskoden blev ændret',
  'activity.kind.password_reset': 'Adgangskoden blev nulstillet med et link på mail',
  'activity.kind.email_change_requested': 'Skift af mailadresse blev bestilt',
  'activity.kind.email_changed': 'Mailadressen blev ændret',
  'activity.kind.identity_linked': 'Loginmetode blev tilføjet',
  'activity.kind.identity_linked.provider': '{provider} blev tilføjet som loginmetode',
  'activity.kind.identity_unlinked': 'Loginmetode blev fjernet',
  'activity.kind.totp_enrolled': 'Tofaktor blev slået til',
  'activity.kind.totp_replaced': 'Godkendelsesappen blev skiftet',
  'activity.kind.totp_removed': 'Tofaktor blev slået fra',
  'activity.kind.recovery_codes_regenerated': 'Nye gendannelseskoder blev lavet',
  'activity.kind.mfa_failed': 'Forkert tofaktorkode',
  'activity.kind.mfa_reset': 'Tofaktor blev nulstillet af support',
  'activity.kind.session_location_changed': 'En session blev brugt fra et nyt netværk eller en ny browser',
  'mailprefs.kind.welcome': 'Velkomstmail',
  'mailprefs.kind.account_activity': 'Meddelelser om kontoaktivitet',
  'mailprefs.kind.class_ready': 'Et hold er klar',
  'mailprefs.intro':
    'Vælg, hvilke valgfrie mails classbeat sender. Links til login, nulstilling af adgangskode og bekræftelse sendes altid. Meddelelser om kontoaktivitet omfatter sikkerhedsbeskeder, for eksempel mislykkede tofaktorforsøg og en ny loginmetode; hvis du slår dem fra, advarer classbeat dig ikke om dem på mail.',
  'mailprefs.all': 'Valgfri mail',
  'export.text':
    'Én fil med din konto, dine hold og versioner, sanglister, job, forbrug og login-historik. Dine forbindelser viser kun deres status: aldrig et token, en adgangskode eller en nøgle.',
  'export.done': 'Dine data blev gemt i en fil.',
  'export.button': 'Download mine data',

  // Account: scan-with-MOWL panel, sign-in methods, two-factor.
  'mowlqr.err.upstream_unavailable':
    'MOWL kunne ikke nås. Prøv igen om nogle minutter, eller brug din adgangskode.',
  'mowlqr.err.rate_limited': 'For mange koder på kort tid. Vent nogle minutter, eller brug din adgangskode.',
  'mowlqr.err.unknown':
    'MOWL svarede på en måde, classbeat ikke havde forventet. Prøv igen senere, eller brug din adgangskode.',
  'mowlqr.err.other': 'Koden virkede ikke. Prøv igen, eller brug din adgangskode.',
  'mowlqr.expired': 'Koden udløb. Vis en ny for at prøve igen.',
  'mowlqr.cancelled': 'Koden blev stoppet.',
  'mowlqr.gone': 'Koden er ikke længere gyldig. Vis en ny.',
  'mowlqr.connectedGone':
    'Koden blev scannet, men forbindelsen blev ikke fundet. Genindlæs siden for at se dine forbindelser.',
  'mowlqr.title': 'Scan med MOWL-appen',
  'mowlqr.how':
    'Åbn MOWL-appen på din telefon, vælg at scanne en kode, og ret kameraet mod denne. classbeat ser aldrig din MOWL-adgangskode.',
  'mowlqr.alt': 'QR-kode til scanning med MOWL-appen',
  'mowlqr.countdown': 'Koden udløber om {time}.',
  'mowlqr.stop': 'Stop',
  'mowlqr.intro':
    'Er du logget ind i MOWL-appen på din telefon? Scan en kode i stedet for at skrive din adgangskode.',
  'mowlqr.showNew': 'Vis en ny kode',
  'mowlqr.show': 'Vis koden',
  'signin.title': 'Loginmetoder',
  'signin.password': 'Adgangskode',
  'signin.passwordSet': 'Angivet',
  'signin.passwordNotSet': 'Ikke angivet',
  'signin.unlink': 'Fjern tilknytning til {provider}',
  'signin.link': 'Tilknyt {provider}',
  'mfa.recoveryTitle': 'Gem disse gendannelseskoder nu.',
  'mfa.recoveryText': '{title} Hver af dem virker én gang i stedet for en appkode. De vises ikke igen.',
  'mfa.recoveryList': 'Gendannelseskoder',
  'mfa.saved': 'Jeg har gemt dem',
  'mfa.setUp': 'Opsæt en godkendelsesapp',
  'mfa.scan': 'Scan denne kode med en godkendelsesapp, eller skriv nøglen i hånden.',
  'mfa.qrLabel': 'QR-kode til din godkendelsesapp',
  'mfa.key': 'Nøgle: {key}',
  'mfa.codeLabel': 'Kode fra appen',
  'mfa.turnOn': 'Slå tofaktorgodkendelse til',
  'mfa.verifyLabel': 'Godkendelses- eller gendannelseskode',
  'mfa.verify': 'Bekræft',
  'mfa.title': 'Tofaktorgodkendelse',
  'mfa.enabled': {
    one: 'Der er opsat en godkendelsesapp. Der er {count} gendannelseskode tilbage.',
    other: 'Der er opsat en godkendelsesapp. Der er {count} gendannelseskoder tilbage.',
  },
  'mfa.newCodes': 'Nye gendannelseskoder',
  'mfa.confirmRemove': 'Bekræft fjernelse af tofaktorgodkendelse',
  'mfa.removeWarning':
    'Uden tofaktorgodkendelse er en stjålet adgangskode nok til at ændre denne konto. Dine gendannelseskoder holder også op med at virke.',
  'mfa.yesRemove': 'Ja, fjern tofaktorgodkendelse',
  'mfa.keep': 'Behold tofaktorgodkendelse',
  'mfa.remove': 'Fjern tofaktorgodkendelse',
  'mfa.offText':
    'Tilføjer en kode fra din telefon til følsomme ændringer på denne side. Administratorer skal opsætte det.',

  // Account: Spotify card and wizard, Swedebeat card.
  'spot.invalidClientId':
    'Det ligner ikke et Spotify Client ID. Det består af 32 bogstaver og cifre (0-9 og a-f) og vises på din apps indstillingsside i Spotify Developer Dashboard.',
  'spot.featureOff': 'Valg af sange er ikke tilgængeligt lige nu.',
  'spot.disconnectText':
    'Når du afbryder forbindelsen, slettes det, classbeat har for Spotify: det gemte login, indstillingen for arbejdsplaylisten og dit samtykke til den, ethvert playlistetrin, der blev ladt uafklaret, og linket mellem hvert hold og den playliste, classbeat lavede til det (selve holdene bliver). Playlister, der allerede er i din Spotify, bliver dér, og classbeat rører dem ikke igen. Spotify giver ikke classbeat mulighed for at afslutte forbindelsen hos sig: fjern classbeats adgang under Apps i dine Spotify-kontoindstillinger.',
  'spot.changeText':
    'classbeat bliver ved med at bruge den nuværende forbindelse, indtil det nye Client ID har virket. Derefter erstatter det det gemte login og glemmer indstillingen for arbejdsplaylisten, så du bliver spurgt om dit samtykke igen; playlisten bliver i din Spotify. Dine hold beholder deres playlister. Hvis den nye app logger en anden Spotify-konto ind, skal du bekræfte skiftet, og classbeat sletter så det, som Afbryd sletter.',
  'spot.title': 'Spotify',
  'spot.notConnected': 'Ikke tilsluttet',
  'spot.setUp': 'Opsæt Spotify',
  'spot.manage': 'Administrer Spotify',
  'spot.loadFailed': 'Spotify-forbindelsen kunne ikke indlæses.',
  'spot.loading': 'Indlæser',
  'spot.connected': 'Spotify er tilsluttet.',
  'spot.connectionTitle': 'Spotify-forbindelse',
  'spot.intro':
    'classbeat forbinder til en Spotify-app, du selv ejer, så grænserne er dine. Følg disse trin én gang.',
  'spot.step1':
    'Åbn Spotify Developer Dashboard (developer.spotify.com/dashboard), og opret en app. Den, der ejer appen, skal have Spotify Premium. En app i Development Mode har en tilladelsesliste på 5 personer: tilføj din egen Spotify-konto (navn og mail) under User Management. Spotifys grænser for endpoints og anmodninger gælder.',
  'spot.step2': 'Kopiér classbeats callback-URL:',
  'spot.copyUrl': 'Kopiér callback-URL',
  'spot.copied': ' Kopieret.',
  'spot.copyFailed': ' Kunne ikke kopiere. Markér adressen, og kopiér den.',
  'spot.step3': 'Sæt den ind under din apps Redirect URIs, og gem.',
  'spot.step4':
    'Kopiér din apps Client ID ind i feltet nedenfor. classbeat har aldrig brug for appens Client Secret og beder ikke om den.',
  'spot.step5': 'Tryk på knappen for at logge ind hos Spotify og give classbeat tilladelse.',
  'spot.confirmHint': 'Angiv Client ID igen, og bekræft for at skifte til den anden Spotify-konto.',
  'spot.clientId': 'Spotify Client ID',
  'spot.confirmSwitch': 'Bekræft og skift konto',
  'spot.connectAgain': 'Tilslut Spotify igen',
  'spot.connect': 'Tilslut Spotify',
  'spot.keepId': 'Behold det nuværende Client ID',
  'spot.changeId': 'Skift Client ID',
  'spot.changeConfirm': 'Bekræft skift af Client ID',
  'spot.continue': 'Fortsæt',
  'spot.disconnectConfirm': 'Bekræft afbrydelse',
  'spot.disconnected': 'Spotify er afbrudt. classbeat bruger ikke længere dets login.',
  'spot.disconnectYes': 'Ja, afbryd Spotify',
  'spot.disconnect': 'Afbryd Spotify',
  'spot.workingTitle': 'Arbejdsplayliste',
  'swed.err.invalid_credentials': 'Swedebeat accepterede ikke den e-mailadresse og adgangskode.',
  'swed.err.upstream_unavailable': 'Swedebeat kunne ikke nås. Prøv igen om nogle minutter.',
  'swed.err.app_key_unavailable': 'Login hos Swedebeat er ikke tilgængeligt på denne server.',
  'swed.err.feature_off': 'Swedebeat er ikke slået til lige nu.',
  'swed.err.unknown': 'Swedebeat svarede på en måde, classbeat ikke havde forventet. Prøv igen senere.',
  'swed.last.needs_reconnect': 'Swedebeat accepterer ikke længere det gemte login. Tilslut igen.',
  'swed.last.decrypt_failed': 'Det gemte login kunne ikke læses. Tilslut igen.',
  'swed.last.rate_limited': 'Swedebeat bad classbeat om at sætte tempoet ned.',
  'swed.last.upstream_unavailable': 'Swedebeat kunne ikke nås sidste gang.',
  'swed.last.other': 'Den seneste brug af denne forbindelse mislykkedes.',
  'swed.deviceInUse':
    'Din Swedebeat-konto er logget ind på en anden enhed, sandsynligvis Swedebeat-appen på din telefon. Hvis du logger ind her alligevel, bliver den enhed logget ud af Swedebeat.',
  'swed.title': 'Swedebeat',
  'swed.connected': 'Swedebeat er tilsluttet.',
  'swed.disconnected': 'Swedebeat er afbrudt. classbeat har ikke længere dets login.',
  'swed.loadFailed': 'Forbindelserne kunne ikke indlæses.',
  'swed.use': 'Jeg bruger Swedebeat',
  'swed.useOn': 'classbeat tilbyder dine Swedebeat-mix, når du designer et hold.',
  'swed.useOff':
    'Slå det til, hvis du underviser med Swedebeat-musik. Indtil da viser classbeat intet om Swedebeat.',
  'swed.notAvailable': 'Login hos Swedebeat er ikke tilgængeligt på denne server.',
  'swed.account': 'Swedebeat-konto',
  'swed.intro':
    'Swedebeat er valgfrit. Log ind med din Swedebeat-konto. classbeat bruger din adgangskode én gang for at hente et login-token og gemmer den ikke.',
  'swed.email': 'Swedebeat-mail',
  'swed.password': 'Swedebeat-adgangskode',
  'swed.again': 'Angiv din adgangskode igen for at logge ind her alligevel.',
  'swed.anyway': 'Log ind her alligevel',
  'swed.connectAgain': 'Tilslut Swedebeat igen',
  'swed.connect': 'Tilslut Swedebeat',
  'swed.keepOther': 'Behold den anden enhed logget ind',
  'swed.disconnectNote':
    'Når du afbryder forbindelsen, slettes det login, classbeat har. Du bliver ikke logget ud af Swedebeat-appen.',
  'swed.confirmLabel': 'Bekræft afbrydelse af Swedebeat',
  'swed.confirmText': 'classbeat glemmer dit Swedebeat-login. Du kan tilslutte igen når som helst.',
  'swed.disconnectYes': 'Ja, afbryd Swedebeat',
  'swed.disconnect': 'Afbryd Swedebeat',

  // Swedebeat match and hand-off on the class page, the reconnect prompt, the account page.
  'swmatch.found': 'Fundet i Swedebeat',
  'swmatch.unsure': 'Usikker match',
  'swmatch.missingChip': 'Findes ikke i Swedebeat',
  'swmatch.notConnected':
    'Spotify, MOWL eller Swedebeat er ikke tilsluttet. Tilslut det først på din kontoside.',
  'swmatch.title': 'Swedebeat-match',
  'swmatch.totals': '{found} fundet, {unsure} usikre, {missing} findes ikke i Swedebeat.',
  'swmatch.length': 'Mixet ville blive {length} langt med Swedebeats versioner.',
  'swmatch.lengthPlanned':
    'Mixet ville blive {length} langt med Swedebeats versioner, mod {planned} planlagt.',
  'swmatch.songs': 'Matchede sange',
  'swmatch.has': 'Swedebeat har: {title}, {artist}',
  'swmatch.hasDuration': 'Swedebeat har: {title}, {artist} ({duration})',
  'swmatch.missing': {
    one: 'Én sang findes ikke i Swedebeat. Hvis du beholder den, mangler den i mixet.',
    other: '{count} sange findes ikke i Swedebeat. Hvis du beholder dem, mangler de i mixet.',
  },
  'swmatch.checkUnsure':
    'Tjek de usikre sange ovenfor. Behold dem kun, hvis Swedebeats version er den samme sang.',
  'swmatch.waitJob': 'Vent først, til den igangværende opgave er færdig.',
  'swmatch.approved': 'Udvalget er godkendt',
  'swmatch.nothingWritten': 'Intet blev skrevet til Swedebeat.',
  'swmatch.approve': 'Godkend dette udvalg',
  'swmatch.replace': 'Erstat de manglende og usikre sange',
  'swstart.blurb':
    'Se, hvilke af disse sange Swedebeat har, så du kan bygge mixet dér. Kun titler og kunstnere slås op, og intet bliver skrevet til din Swedebeat-konto.',
  'swstart.connectFirst': 'Tilslut først Swedebeat på din kontoside',
  'swstart.match': 'Match med Swedebeat',
  'swstart.loadFailed': 'Swedebeat-matchet kunne ikke indlæses. Genindlæs siden.',
  'swstart.loading': 'Indlæser Swedebeat-matchet',
  'swreconnect.text':
    'Swedebeat eller MOWL accepterer ikke længere det gemte login, så intet blev sendt. Tilslut igen, og kom derefter tilbage til dette hold.',
  'swreconnect.link': 'Tilslut igen på din kontoside',
  'swnext.title': 'Næste trin, i Swedebeat-appen',
  'swnext.ok': 'Det er dit mix. Dets længde passer til dit udvalg.',
  'swnext.tooShort': 'Mixet er kortere end forventet.',
  'swnext.tooShortWhere': 'Mixet er kortere end forventet. Sangene {positions} mangler måske.',
  'swnext.tooLong': 'Mixet er længere end forventet. En sang kan være tilføjet eller gentaget.',
  'swnext.notFound':
    'Swedebeat har ikke vist et nyt mix endnu. Gør det færdigt i appen, lad det synkronisere, og tjek derefter igen.',
  'swnext.stopped': 'Tjekket blev stoppet. Tjek igen.',
  'swnext.notNow': 'Ikke nu. Dit udvalg forbliver godkendt; kom tilbage til dette hold, når du er klar.',
  'swnext.showAgain': 'Vis trinnene igen',
  'swnext.playlist': 'Playliste: {name}.',
  'swnext.facts': '{count} sange, cirka {length} langt som mix.',
  'swnext.step1': 'Åbn Swedebeat-appen.',
  'swnext.step2Named': 'Match playlisten »{name}«.',
  'swnext.step2': 'Match din playliste.',
  'swnext.step3': 'Opret mixet.',
  'swnext.step4': 'Lad det synkronisere, og kom derefter tilbage hertil.',
  'swnext.expiry':
    'Du kan forlade siden og komme tilbage. Dette trin forbliver åbent til {when}; derefter starter du det igen.',
  'swnext.expired': 'Dette trin er udløbet. Tryk på knappen for at starte det igen.',
  'swnext.checking': 'Tjekker dine mix i MOWL',
  'swnext.prefOff':
    '»Jeg bruger Swedebeat« er slået fra. Slå det til på {link} for at starte dette trin eller designe holdet på mixet. Et trin, du allerede har startet, kan stadig tjekkes.',
  'swnext.accountPage': 'din kontoside',
  'swnext.prefUnknown':
    'classbeat kunne ikke tjekke din Swedebeat-indstilling. Genindlæs siden for at designe holdet på dette mix.',
  'swnext.verified': 'Mixet er verificeret',
  'swnext.design': 'Design holdet på dette mix',
  'swnext.checkAgain': 'Tjek igen',
  'swnext.made': 'Jeg lavede mixet',
  'swnext.later': 'Ikke nu',
  'swnext.right': 'Det er det rigtige mix',
  'swnext.mixesFailed': 'Dine Swedebeat-mix kunne ikke indlæses fra MOWL. Tjek igen senere.',
  'swnext.mixesLoading': 'Indlæser dine mix',
  'swnext.which': 'Hvilket lavede du?',
  'swnext.new': 'Ny',
  'account.oauth.linked': 'Loginmetoden er tilknyttet din konto.',
  'account.oauth.reauthed': 'Tak, du er bekræftet. Gentag den ændring, du var i gang med.',
  'account.tab.connections': 'Forbindelser',
  'account.tab.plan': 'Plan og forbrug',
  'account.tab.security': 'Login og sikkerhed',
  'account.tab.data': 'Dine data',
  'account.tab.appearance': 'Udseende',
  'account.heading': 'Din konto',
  'account.tabs': 'Konto',
  'account.usageDetails': 'Vis forbrugsdetaljer',
  'account.email': 'Mail',
  'account.signOut': 'Log ud',
  'account.language': 'Sprog',
  'account.theme': 'Tema',
  'account.themeHelp': 'Mørkt er standard. Samme som enheden følger din telefon eller computer.',
}

export default da
