import type { Catalogue } from './en'

// Danish text of the privacy notice and the terms (see legal.en.ts).
export const legalDa: Catalogue = {
  'legal.link.privacy': 'Privatliv',
  'legal.link.terms': 'Vilkår',
  'legal.footer.label': 'Juridisk information',
  'legal.read': 'Læs {privacy} og {terms}.',
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
  'legal.controller.hosting': 'Hvor tjenesten drives',

  'privacy.title': 'Privatlivspolitik',
  'privacy.lead':
    'classbeat er en privat beta for undervisere i indendørscykling. Her står, hvad vi gemmer om dig, hvorfor, hvor længe, og hvad du selv kan gøre.',

  'privacy.who.title': 'Hvem vi er',
  'privacy.who.p1':
    'Den, der er ansvarlig for dine oplysninger (den dataansvarlige), er angivet nedenfor. Du kan skrive til os på {email} om alt på denne side.',

  'privacy.data.title': 'Hvad vi gemmer',
  'privacy.data.p1': 'Vi gemmer det, tjenesten skal bruge for at virke, og intet til reklamer.',
  'privacy.data.li1':
    'Din konto: din e-mailadresse, din adgangskode kun som en envejs-hash og hvilke loginmetoder du bruger.',
  'privacy.data.li2':
    'Dine logins: de enheder du har logget ind fra, tidspunktet og IP-adressen for en session.',
  'privacy.data.li3':
    'Dine hold: det du beskriver, samtalen med assistenten, sangene og de færdige hold med tidligere versioner.',
  'privacy.data.li4':
    'Dine forbindelser til MOWL, Spotify og Swedebeat: kun det login-token, hver tjeneste gav os, gemt krypteret. Adgangskoderne du skriver til MOWL eller Swedebeat gemmer vi aldrig.',
  'privacy.data.li5':
    'Dit forbrug: hvor mange hold du har lavet, og hvad assistenten kostede for dem, hvilket styrer din månedlige grænse.',
  'privacy.data.li6': 'Dine e-mailindstillinger.',

  'privacy.model.title': 'Hvad assistenten ser',
  'privacy.model.p1':
    'For at udforme et hold sender vi det, du har skrevet om det, og et resumé af sangene (afspilningslistens navn og for hver sang titel, kunstner, tempo og længde) til en udbyder af AI-modeller.',
  'privacy.model.p2':
    "Vi sender aldrig din e-mailadresse, dit navn, adgangskoder, tokens eller konto- eller tjeneste-id'er. Assistenten foreslår kun: intet gemmes i MOWL eller Spotify, før du har godkendt det.",

  'privacy.processors.title': 'Hvem andre håndterer dine oplysninger',
  'privacy.processors.p1':
    'Vi bruger disse tjenester til at drive classbeat. Hver får kun det, den skal bruge til sin opgave.',
  'privacy.processors.li1':
    'En udbyder af AI-modeller (Anthropic) får holdteksten og sangresuméet ovenfor for at udforme dit hold. Den er uden for EU, så det er en overførsel til et tredjeland.',
  'privacy.processors.li2':
    'MOWL, Spotify og Swedebeat får kun forespørgsler, når du tilslutter en konto eller gemmer noget der. De har deres egne privatlivspolitikker.',
  'privacy.processors.li3':
    'Hvis du vælger at logge ind med Google, GitHub eller Microsoft, fortæller den udbyder os din bekræftede e-mailadresse.',
  'privacy.processors.li4':
    'Vores e-mailtjeneste sender de mails, vi sender til dig (bekræftelse ved tilmelding, nulstilling af adgangskode og lignende).',
  'privacy.processors.li5':
    'Vores driftsleverandør kører serverne. Hvor de står, er angivet i boksen ovenfor.',
  'privacy.processors.li6':
    'Din browser henter albumcovers direkte fra Spotifys billedservere, som derfor ser din IP-adresse og browser. Vi sender ingen referrer, ingen classbeat-cookie og ingen classbeat-data med den forespørgsel.',
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
    'Hvert cookienavn begynder med __Host-, hvilket betyder, at den kun sendes over en sikker forbindelse til denne side. Vi gemmer også dit valgte sprog i browseren (cb-locale); det sendes aldrig til os.',

  'privacy.retention.title': 'Hvor længe vi gemmer det',
  'privacy.retention.li1':
    'Loginsessioner: fjernes, når de udløber eller efter 7 dage uden brug; IP-adressen ryddes efter 30 dage.',
  'privacy.retention.li2': 'Loginhistorik: 30 dage.',
  'privacy.retention.li3': 'Kendte enheder: fjernes efter 90 dage uden brug.',
  'privacy.retention.li4': 'Sikkerhedslog: 7 dage.',
  'privacy.retention.li5': 'Invitationer: fjernes 30 dage efter, de er brugt eller udløbet.',
  'privacy.retention.li6':
    'Din konto, dine hold, forbindelser og dit forbrug: indtil du sletter dem eller din konto.',
  'privacy.retention.p1':
    'Når du sletter din konto, slettes alt ovenfor med den. Kopier i sikkerhedskopier af vores database gemmes i en begrænset tid og udløber derefter; slettede oplysninger kan blive i en sikkerhedskopi indtil da.',

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
    'De hold, du laver, er dine. Du er ansvarlig for, at du har ret til at bruge musikken i dem. Vi gemmer kun det, du godkender, på dine konti, og altid som privat.',
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
    "Svensk lov gælder for disse vilkår, uden at fjerne de ufravigelige forbrugerbeskyttelsesregler i det EU- eller EØS-land, hvor du bor; GDPR og EU's forbrugerlovgivning gælder som sædvanligt. Tvister afgøres af de svenske domstole, men som forbruger kan du også anlægge sag ved domstolene i dit eget EU-land, og du kan stadig henvende dig til dit nationale forbrugerklagenævn.",
}
