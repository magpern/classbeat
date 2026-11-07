import type { Catalogue } from './en'

// Danish text of the privacy notice and the terms (see legal.en.ts).
export const legalDa: Catalogue = {
  'legal.link.privacy': 'Privatliv',
  'legal.link.terms': 'Vilkår',
  'legal.footer.label': 'Juridisk information',
  'legal.read': 'Læs {privacy} og {terms}.',
  'cookies.more': 'Mere i {privacy}.',
  'legal.inline.privacy': 'privatlivspolitikken',
  'legal.inline.terms': 'brugsvilkårene',
  'legal.updated': 'Senest opdateret 7. november 2026.',
  'legal.contactEmail': 'support@classbeat.eu',
  'legal.controller.title': 'Hvem der er ansvarlig',
  'legal.controller.note':
    'Disse oplysninger er ikke udfyldt endnu og skal udfyldes, før betaen åbnes for offentligheden.',
  'legal.controller.name': 'Dataansvarlig',
  'legal.controller.address': 'Postadresse',
  'legal.controller.company': 'Virksomheds- eller registreringsnummer',
  'legal.controller.value': '[Udfyldes af ejeren]',
  'legal.controller.hosting': 'Driftsleverandør, placering og eventuel proxytjeneste',

  'privacy.title': 'Privatlivspolitik',
  'privacy.lead':
    'classbeat er en privat beta for undervisere i indendørscykling. Her står, hvad vi gemmer om dig, hvorfor, hvor længe, og hvad du selv kan gøre.',

  'privacy.who.title': 'Hvem vi er',
  'privacy.who.p1':
    'Den, der er ansvarlig for dine oplysninger (den dataansvarlige), er angivet nedenfor. Du kan skrive til os på {email} om alt på denne side.',

  'privacy.data.title': 'Hvad vi gemmer',
  'privacy.data.p1': 'Vi gemmer det, tjenesten skal bruge for at virke, og intet til reklamer.',
  'privacy.data.li1':
    'Din konto: din e-mailadresse, din adgangskode kun som en envejs-hash, de loginmetoder du bruger (med det konto-id, et login med Google, GitHub eller Microsoft giver os), dine indstillinger for tofaktorlogin og dit sprog.',
  'privacy.data.li2':
    'Dine logins: de enheder du har logget ind fra, tidspunktet og IP-adressen for en session.',
  'privacy.data.li3':
    'Dine hold: det du beskriver, samtalen med assistenten, sangene og de færdige hold med tidligere versioner.',
  'privacy.data.li4':
    'Dine forbindelser til MOWL, Spotify og Swedebeat: det login-token, hver tjeneste gav os, gemt krypteret, og den kontoidentifikator, tjenesten oplyser (din e-mailadresse hos Swedebeat, dit Spotify-bruger-id og Spotifys Client ID). Swedebeat giver os også et enheds-id, gemt krypteret. Adgangskoderne du skriver til MOWL eller Swedebeat gemmer vi aldrig.',
  'privacy.data.li5':
    'Dit forbrug: hvor mange hold du har lavet, hvad assistenten kostede for dem, og de kreditter du har brugt og fået, hvilket styrer din månedlige grænse.',
  'privacy.data.li6': 'Dine e-mailindstillinger.',
  'privacy.data.li7': 'Din plan: den plan du har, og eventuelle koder du har indløst.',

  'privacy.basis.title': 'Hvorfor vi må bruge dem',
  'privacy.basis.p1': 'Loven kræver et grundlag for hver måde, vi bruger dine oplysninger på. Vores er:',
  'privacy.basis.li1':
    'For at levere den tjeneste, du har tilmeldt dig (din konto, dine hold, forbindelser, forbrugsgrænser og de mails, der skal til for driften): det er nødvendigt for at opfylde vores aftale med dig i brugsvilkårene.',
  'privacy.basis.li2':
    'For at holde tjenesten sikker og fair (loginsessioner og loginhistorik, sikkerhedsloggen, webserverens logge, grænserne mod misbrug og sikkerhedstjekket på loginsiderne): vores legitime interesse i at beskytte tjenesten og dens brugere.',
  'privacy.basis.li3':
    'For at opfylde retlige forpligtelser, der gælder for os, hvis der er nogen: retlig forpligtelse.',

  'privacy.model.title': 'Hvad assistenten ser',
  'privacy.model.p1':
    'For at udforme et hold sender vi til en udbyder af AI-modeller det, du har skrevet om det, dine ændringsønsker og tidligere ønsker, de udkast og tidslinjer, der er lavet indtil nu, og et resumé af sangene (afspilningslistens navn og for hver sang oplysninger som titel, kunstner, tempo og længde).',
  'privacy.model.p2':
    "Vi sender aldrig din e-mailadresse, dit navn, adgangskoder, tokens eller konto- eller tjeneste-id'er. Assistenten foreslår kun: classbeat gemmer kun i MOWL eller Spotify det, du godkender, eller det, du på forhånd har tilladt med dit engangssamtykke til en privat sangpulje i din Spotify.",

  'privacy.processors.title': 'Hvem andre håndterer dine oplysninger',
  'privacy.processors.p1':
    'Vi bruger disse tjenester til at drive classbeat. Hver får kun det, den skal bruge til sin opgave.',
  'privacy.processors.li1':
    'En udbyder af AI-modeller (Anthropic) får holdteksten og sangresuméet ovenfor for at udforme dit hold. Den er uden for EU, så det er en overførsel til et tredjeland.',
  'privacy.processors.li2':
    'MOWL, Spotify og Swedebeat får forespørgsler via din forbindelse, når du tilslutter en konto, når classbeat læser din musik for at udforme et hold (dine afspilningslister og deres analyse, søgninger efter sange og mixes), og når det gemmer det, du har godkendt. De har deres egne privatlivspolitikker.',
  'privacy.processors.li3':
    'Hvis du vælger at logge ind med Google, GitHub eller Microsoft, fortæller den udbyder os din bekræftede e-mailadresse og dit konto-id.',
  'privacy.processors.li4':
    'Vores e-mailtjeneste sender de mails, vi sender til dig (bekræftelse ved tilmelding, nulstilling af adgangskode og lignende).',
  'privacy.processors.li5':
    'Vores driftsleverandør kører serverne, og en eventuel proxytjeneste foran dem ser hver forespørgsel og IP-adresse. Navne og placering er angivet i boksen ovenfor.',
  'privacy.processors.li6':
    'Din browser henter albumcovers direkte fra Spotifys billedservere, som derfor ser din IP-adresse og browser. Vi sender ingen referrer, ingen classbeat-cookie og ingen classbeat-data med den forespørgsel.',
  'privacy.processors.li7':
    'På siderne til login, tilmelding og nulstilling af adgangskode, og på siden der spørger efter din e-mailadresse efter login med Google, GitHub eller Microsoft, tjekker Cloudflare Turnstile, at det er et menneske, ikke et program, der bruger formularen. Din browser henter det fra Cloudflare, som ser din IP-adresse og signaler fra browser og enhed; vores server sender Cloudflare tjekkets resultatkode og din IP-adresse, aldrig din e-mail eller adgangskode. Cloudflare kan også bruge signalerne til at forbedre sin beskyttelse mod bots. Virksomheden er baseret i USA, så det er en overførsel til et tredjeland.',
  'privacy.processors.p2': 'Vi sælger ikke dine oplysninger og bruger dem ikke til reklamer.',

  'privacy.cookies.title': 'Cookies og lagring i browseren',
  'privacy.cookies.p1':
    'Vi sætter kun de cookies, siden skal bruge for at logge dig ind og holde den sikker. De bruges ikke til sporing eller reklamer, så der er intet at fravælge.',
  'privacy.cookies.li1':
    'classbeat_session: holder dig logget ind, højst 30 dage, og udløber efter 7 dage uden brug.',
  'privacy.cookies.li2': 'classbeat_device: genkender en enhed, du har logget ind fra, i 1 år.',
  'privacy.cookies.li3': 'classbeat_csrf: beskytter formularer mod forfalskning, i 30 dage.',
  'privacy.cookies.li4':
    'classbeat_oauth: holder status for et login med Google, GitHub, Microsoft eller Spotify, mens det står på, i 10 minutter.',
  'privacy.cookies.li5':
    'classbeat_pending: holder et login, der stadig mangler et trin fra dig, i 24 timer.',
  'privacy.cookies.p2':
    'Hvert cookienavn begynder med __Host-, hvilket betyder, at den kun sendes over en sikker forbindelse til denne side.',
  'privacy.cookies.p3':
    'Vi gemmer også nogle få ting i selve browseren, ingen af dem til sporing. Dit sprog (cb-locale) sendes også til os, når du tilmelder dig eller skifter sprog, og gemmes som kontoens sprog. Dit lyse eller mørke tema gemmes som cb-theme, og cb-cookie-notice husker, at du har lukket cookiemeddelelsen. Mens du arbejder i en fane, gemmes formularen, du lige har sendt, kun i den fane, så du kan starte forfra uden at skrive den to gange (classbeat.design-input.*, classbeat.pick-input.* og classbeat.design-again-key.*); den er væk, når du lukker fanen.',
  'privacy.cookies.p4':
    'Sikkerhedstjekket kører i en ramme fra challenges.cloudflare.com og kan bruge cookies eller lokal lagring dér, kun til tjekket; classbeat sætter ingen til det.',

  'privacy.retention.title': 'Hvor længe vi gemmer det',
  'privacy.retention.li1':
    'Loginsessioner: fjernes, når de udløber eller efter 7 dage uden brug; IP-adressen ryddes efter 30 dage.',
  'privacy.retention.li2': 'Loginhistorik: 30 dage.',
  'privacy.retention.li3': 'Kendte enheder: fjernes efter 90 dage uden brug.',
  'privacy.retention.li4':
    'Sikkerhedslog: 7 dage (kan indstilles fra 1 til 30 dage; politikken holdes i trit med indstillingen).',
  'privacy.retention.li5': 'Invitationer: fjernes 30 dage efter, de er brugt eller udløbet.',
  'privacy.retention.li6':
    'Din konto, dine hold, forbindelser og dit forbrug: indtil du sletter dem eller din konto.',
  'privacy.retention.li7':
    'Webserverens adgangslog, som indeholder din IP-adresse, tidspunktet og den adresse, der blev bedt om: op til 30 dage.',
  'privacy.retention.p1':
    'Når du sletter din konto, slettes alt ovenfor med den. Kopier i sikkerhedskopier af vores database gemmes i en begrænset tid og udløber derefter; slettede oplysninger kan blive i en sikkerhedskopi indtil da.',
  'privacy.retention.p2':
    'Det, vi har gemt på dine MOWL- og Spotify-konti efter din anmodning, for eksempel workouts og afspilningslister, bliver i de tjenester, når du sletter din konto; slet det dér. Er du administrator, bliver din e-mailadresse også i administrationens revisionslog.',

  'privacy.rights.title': 'Dine rettigheder',
  'privacy.rights.p1':
    'Du kan selv se, rette, eksportere og slette dine oplysninger. På din kontoside giver Download mine data dig én fil med din konto, dine hold, sanglister, dit forbrug og din loginhistorik, og Slet min konto fjerner kontoen og dens oplysninger. Der kan du også skifte e-mailadresse og adgangskode.',
  'privacy.rights.p2':
    'Du har også ret til at bede os begrænse, hvordan vi bruger dine oplysninger, til at gøre indsigelse og til at få en kopi. Skriv til {email}. Vi svarer inden for en måned.',
  'privacy.rights.p3':
    'Hvis du mener, at vi håndterer dine oplysninger forkert, kan du klage til tilsynsmyndigheden, hvor du bor: i Danmark Datatilsynet, i Sverige Integritetsskyddsmyndigheten (IMY), eller myndigheden i et andet EU-land.',

  'privacy.changes.title': 'Ændringer',
  'privacy.changes.p1':
    'Hvis vi ændrer denne politik på en måde, der betyder noget for dig, siger vi det i tjenesten. Datoen ovenfor viser den seneste version. Hvordan du må bruge classbeat står i {terms}.',

  'terms.title': 'Brugsvilkår',
  'terms.lead':
    'Det er de korte og enkle regler for at bruge den private beta af classbeat. Når du opretter en konto, accepterer du dem.',

  'terms.service.title': 'Tjenesten',
  'terms.service.p1':
    'classbeat hjælper undervisere i indendørscykling med at udforme hold ved hjælp af en AI-assistent og, når du godkender, gemme dem i din MOWL-konto og oprette afspilningslister på din Spotify-konto. Det er en beta kun for inviterede, som den er, og den kan ændres eller ophøre.',
  'terms.service.p2':
    'classbeat er ikke lavet af eller tilknyttet MOWL, Spotify eller Swedebeat. Din brug af de tjenester sker efter deres egne vilkår.',

  'terms.account.title': 'Din konto',
  'terms.account.li1': 'Angiv en rigtig e-mailadresse, og hold dine loginoplysninger for dig selv.',
  'terms.account.li2': 'Du er ansvarlig for det, der sker via din konto.',
  'terms.account.li3': 'Én person, én konto. Del ikke din invitationskode.',

  'terms.content.title': 'Dine hold og din musik',
  'terms.content.p1':
    'De hold, du laver, er dine. Du er ansvarlig for, at du har ret til at bruge musikken i dem. Vi gemmer kun det, du godkender eller på forhånd har tilladt med dit engangssamtykke, på dine konti, og altid som privat.',
  'terms.content.p2':
    'Assistenten kan lave fejl: sange, tider og indsats er forslag. Tjek et hold, før du underviser i det.',

  'terms.use.title': 'Hvad du ikke må',
  'terms.use.li1': 'Forsøg ikke at ødelægge, overbelaste eller omgå tjenesten eller dens grænser.',
  'terms.use.li2': 'Brug den ikke til at bryde loven eller vilkårene hos MOWL, Spotify eller Swedebeat.',
  'terms.use.li3': 'Brug ikke en andens konto.',

  'terms.liability.title': 'Tilgængelighed og ansvar',
  'terms.liability.p1':
    'Vi gør vores bedste for at holde classbeat kørende, men lover ikke, at den altid er tilgængelig eller fejlfri. I det omfang loven tillader det, er vi ikke ansvarlige for tab, der skyldes brug af en betatjeneste. Intet her begrænser rettigheder, du har efter loven som forbruger.',

  'terms.ending.title': 'Ophør',
  'terms.ending.p1':
    'Du kan stoppe når som helst ved at slette din konto. Vi kan lukke en konto, der bryder vilkårene eller skader tjenesten. Hvad vi gør med dine oplysninger, står i {privacy}.',

  'terms.law.title': 'Ændringer og lov',
  'terms.law.p1':
    'Vi kan opdatere vilkårene og siger det i tjenesten, før en ændring, der betyder noget for dig, træder i kraft. Spørgsmål går til {email}.',
  'terms.law.p2':
    'Svensk lov gælder for disse vilkår, uden at fjerne de ufravigelige forbrugerbeskyttelsesregler i det EU- eller EØS-land, hvor du bor. Er du forbruger i EU eller EØS, kan du indbringe en tvist for domstolene i det land, hvor du bor, eller for de svenske domstole, og vi kan kun anlægge sag mod dig ved domstolene i det land, hvor du bor. Du kan også henvende dig til dit nationale forbrugerklagenævn.',
}
