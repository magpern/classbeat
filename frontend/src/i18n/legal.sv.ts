import type { Catalogue } from './en'

// Swedish text of the privacy notice and the terms (see legal.en.ts).
export const legalSv: Catalogue = {
  'legal.link.privacy': 'Integritet',
  'legal.link.terms': 'Villkor',
  'legal.footer.label': 'Juridisk information',
  'legal.read': 'Läs {privacy} och {terms}.',
  'cookies.more': 'Mer i {privacy}.',
  'legal.inline.privacy': 'integritetspolicyn',
  'legal.inline.terms': 'användarvillkoren',
  'legal.updated': 'Senast uppdaterad 7 november 2026.',
  'legal.contactEmail': 'support@classbeat.eu',
  'legal.controller.title': 'Vem som ansvarar',
  'legal.controller.note':
    'De här uppgifterna är inte ifyllda ännu och måste fyllas i innan betan öppnas för allmänheten.',
  'legal.controller.name': 'Personuppgiftsansvarig',
  'legal.controller.address': 'Postadress',
  'legal.controller.company': 'Företags- eller registreringsnummer',
  'legal.controller.value': '[Fylls i av ägaren]',
  'legal.controller.hosting': 'Driftleverantör, plats och eventuell proxytjänst',

  'privacy.title': 'Integritetspolicy',
  'privacy.lead':
    'classbeat är en privat beta för instruktörer i inomhuscykling. Här står vad vi sparar om dig, varför, hur länge och vad du själv kan göra.',

  'privacy.who.title': 'Vilka vi är',
  'privacy.who.p1':
    'Den som ansvarar för dina uppgifter (personuppgiftsansvarig) anges nedan. Du kan skriva till oss på {email} om allt som står på den här sidan.',

  'privacy.data.title': 'Vad vi sparar',
  'privacy.data.p1': 'Vi sparar det tjänsten behöver för att fungera och ingenting för reklam.',
  'privacy.data.li1':
    'Ditt konto: din e-postadress, ditt lösenord som enbart en enkelriktad hash, de inloggningssätt du använder (med det konto-ID som en inloggning med Google, GitHub eller Microsoft ger oss), dina inställningar för tvåstegsverifiering och ditt språk.',
  'privacy.data.li2':
    'Dina inloggningar: de enheter du loggat in från, tidpunkten och IP-adressen för en session.',
  'privacy.data.li3':
    'Dina pass: det du beskriver, samtalet med assistenten, låtarna och de färdiga passen med tidigare versioner.',
  'privacy.data.li4':
    'Dina anslutningar till MOWL, Spotify och Swedebeat: den inloggningstoken varje tjänst gav oss, lagrad krypterad, och det konto-ID som tjänsten anger (din e-postadress hos Swedebeat, ditt Spotify-användar-ID och Spotifys Client ID). Swedebeat ger oss också ett enhets-ID, lagrat krypterat. Lösenorden du skriver för MOWL eller Swedebeat sparar vi aldrig.',
  'privacy.data.li5':
    'Din användning: hur många pass du gjort, assistentens kostnad för dem och de krediter du använt och fått, vilket styr din månadsgräns.',
  'privacy.data.li6': 'Dina e-postinställningar.',
  'privacy.data.li7': 'Din plan: den plan du har och eventuella koder du löst in.',

  'privacy.basis.title': 'Varför vi får använda dem',
  'privacy.basis.p1': 'Lagen kräver en grund för varje sätt vi använder dina uppgifter. Våra är:',
  'privacy.basis.li1':
    'För att tillhandahålla tjänsten du registrerade dig för (ditt konto, dina pass, anslutningar, användningsgränser och de mejl som behövs för driften): det behövs för att fullgöra vårt avtal med dig i användarvillkoren.',
  'privacy.basis.li2':
    'För att hålla tjänsten säker och rättvis (inloggningssessioner och inloggningshistorik, säkerhetsloggen, webbserverns loggar, gränserna mot missbruk och säkerhetskontrollen på inloggningssidorna): vårt berättigade intresse av att skydda tjänsten och dess användare.',
  'privacy.basis.li3':
    'För att uppfylla rättsliga skyldigheter som gäller för oss, om några: rättslig förpliktelse.',

  'privacy.model.title': 'Vad assistenten ser',
  'privacy.model.p1':
    'För att utforma ett pass skickar vi till en leverantör av AI-modeller det du skrivit om det, dina ändringsönskemål och tidigare önskemål, de utkast och tidslinjer som gjorts hittills och en sammanfattning av låtarna (spellistans namn och för varje låt uppgifter som titel, artist, tempo och längd).',
  'privacy.model.p2':
    'Vi skickar aldrig din e-postadress, ditt namn, lösenord, tokens eller några konto- eller tjänst-ID:n. Assistenten föreslår bara: classbeat sparar i MOWL eller Spotify bara det du godkänner, eller det du tillåtit i förväg genom ditt engångssamtycke till en privat låtpool i ditt Spotify.',

  'privacy.processors.title': 'Vilka andra hanterar dina uppgifter',
  'privacy.processors.p1':
    'Vi använder de här tjänsterna för att driva classbeat. Var och en får bara det den behöver för sin uppgift.',
  'privacy.processors.li1':
    'En leverantör av AI-modeller (Anthropic) får texten om passet och låtsammanfattningen ovan för att utforma ditt pass. Den finns utanför EU, så det är en överföring till tredje land.',
  'privacy.processors.li2':
    'MOWL, Spotify och Swedebeat får förfrågningar via din anslutning när du ansluter ett konto, när classbeat läser din musik för att utforma ett pass (dina spellistor och deras analys, sökningar efter låtar och mixar) och när det sparar det du godkänt. De har egna integritetspolicyer.',
  'privacy.processors.li3':
    'Om du väljer att logga in med Google, GitHub eller Microsoft talar den leverantören om din verifierade e-postadress och ditt konto-ID för oss.',
  'privacy.processors.li4':
    'Vår e-posttjänst skickar de mejl vi skickar till dig (bekräftelse vid registrering, återställning av lösenord och liknande).',
  'privacy.processors.li5':
    'Vår driftleverantör kör servrarna, och en eventuell proxytjänst framför dem ser varje förfrågan och IP-adress. Namn och plats anges i rutan ovan.',
  'privacy.processors.li6':
    'Din webbläsare hämtar albumomslag direkt från Spotifys bildservrar, som därför ser din IP-adress och webbläsare. Vi skickar ingen referrer, ingen classbeat-cookie och inga classbeat-uppgifter med den förfrågan.',
  'privacy.processors.li7':
    'På sidorna för inloggning, registrering, återställning av lösenord och e-post kontrollerar Cloudflare Turnstile att det är en person, inte ett program, som använder formuläret. Din webbläsare hämtar den från Cloudflare, som ser din IP-adress och signaler från webbläsare och enhet; vår server skickar till Cloudflare kontrollens resultatkod och din IP-adress, aldrig din e-postadress eller ditt lösenord. Cloudflare kan också använda signalerna för att förbättra sitt skydd mot robotar. Företaget finns i USA.',
  'privacy.processors.p2': 'Vi säljer inte dina uppgifter och använder dem inte för reklam.',

  'privacy.cookies.title': 'Cookies och lagring i webbläsaren',
  'privacy.cookies.p1':
    'Vi sätter bara de cookies som webbplatsen behöver för att logga in dig och hålla den säker. De används inte för spårning eller reklam, så det finns inget att tacka nej till.',
  'privacy.cookies.li1':
    'classbeat_session: håller dig inloggad, högst 30 dagar, och upphör efter 7 dagar utan användning.',
  'privacy.cookies.li2': 'classbeat_device: känner igen en enhet du loggat in från, i 1 år.',
  'privacy.cookies.li3': 'classbeat_csrf: skyddar formulär mot förfalskning, i 30 dagar.',
  'privacy.cookies.li4':
    'classbeat_oauth: håller läget för en inloggning med Google, GitHub, Microsoft eller Spotify medan den pågår, i 10 minuter.',
  'privacy.cookies.li5':
    'classbeat_pending: håller en inloggning som ännu kräver ett steg av dig, i 24 timmar.',
  'privacy.cookies.p2':
    'Varje cookienamn börjar med __Host-, vilket betyder att den bara skickas över en säker anslutning till den här webbplatsen.',
  'privacy.cookies.p3':
    'Vi sparar också några saker i själva webbläsaren, ingen av dem för spårning. Ditt språk (cb-locale) skickas också till oss när du registrerar dig eller byter språk, och sparas som kontots språk. Ditt ljusa eller mörka tema sparas som cb-theme, och cb-cookie-notice kommer ihåg att du har stängt kakmeddelandet. Medan du arbetar i en flik sparas formuläret du just skickade bara i den fliken, så att du kan börja om utan att skriva det två gånger (classbeat.design-input.*, classbeat.pick-input.* och classbeat.design-again-key.*); det är borta när du stänger fliken.',
  'privacy.cookies.p4':
    'Säkerhetskontrollen körs i en ram från challenges.cloudflare.com och kan använda cookies eller lokal lagring där, bara för kontrollen; classbeat sätter inga för den.',

  'privacy.retention.title': 'Hur länge vi sparar det',
  'privacy.retention.li1':
    'Inloggningssessioner: tas bort när de går ut eller efter 7 dagar utan användning; IP-adressen rensas efter 30 dagar.',
  'privacy.retention.li2': 'Inloggningshistorik: 30 dagar.',
  'privacy.retention.li3': 'Kända enheter: tas bort efter 90 dagar utan användning.',
  'privacy.retention.li4':
    'Säkerhetslogg: 7 dagar (kan ställas in från 1 till 30 dagar; policyn hålls i takt med inställningen).',
  'privacy.retention.li5': 'Inbjudningar: tas bort 30 dagar efter att de använts eller gått ut.',
  'privacy.retention.li6':
    'Ditt konto, dina pass, anslutningar och din användning: tills du raderar dem eller ditt konto.',
  'privacy.retention.li7':
    'Webbserverns åtkomstlogg, som innehåller din IP-adress, tidpunkten och den adress som begärdes: upp till 30 dagar.',
  'privacy.retention.p1':
    'När du raderar ditt konto raderas allt ovan med det. Kopior i säkerhetskopior av vår databas sparas en begränsad tid och går sedan ut; raderade uppgifter kan finnas kvar i en säkerhetskopia till dess.',
  'privacy.retention.p2':
    'Sådant vi sparat i dina MOWL- och Spotify-konton på din begäran, till exempel pass och spellistor, finns kvar i de tjänsterna när du raderar ditt konto; radera dem där. Om du är administratör finns din e-postadress också kvar i administrationens granskningslogg.',

  'privacy.rights.title': 'Dina rättigheter',
  'privacy.rights.p1':
    'Du kan själv se, rätta, exportera och radera dina uppgifter. På din kontosida ger Ladda ner mina data dig en fil med ditt konto, dina pass, låtlistor, din användning och inloggningshistorik, och Radera mitt konto tar bort kontot och dess uppgifter. Där kan du också byta e-postadress och lösenord.',
  'privacy.rights.p2':
    'Du har också rätt att be oss begränsa hur vi använder dina uppgifter, att invända mot det och att få en kopia. Skriv till {email}. Vi svarar inom en månad.',
  'privacy.rights.p3':
    'Om du tycker att vi hanterar dina uppgifter fel kan du klaga hos tillsynsmyndigheten där du bor: i Sverige Integritetsskyddsmyndigheten (IMY), i Danmark Datatilsynet, eller myndigheten i ett annat EU-land.',

  'privacy.changes.title': 'Ändringar',
  'privacy.changes.p1':
    'Om vi ändrar den här policyn på ett sätt som spelar roll för dig säger vi det i tjänsten. Datumet ovan visar senaste versionen. Hur du får använda classbeat beskrivs i {terms}.',

  'terms.title': 'Användarvillkor',
  'terms.lead':
    'Det här är de korta och enkla reglerna för att använda den privata betan av classbeat. När du skapar ett konto godkänner du dem.',

  'terms.service.title': 'Tjänsten',
  'terms.service.p1':
    'classbeat hjälper instruktörer i inomhuscykling att utforma pass med hjälp av en AI-assistent och, när du godkänner, spara dem i ditt MOWL-konto och skapa spellistor i ditt Spotify-konto. Det är en beta enbart för inbjudna, som den är, och den kan ändras eller upphöra.',
  'terms.service.p2':
    'classbeat är inte gjort av eller knutet till MOWL, Spotify eller Swedebeat. Din användning av de tjänsterna sker enligt deras egna villkor.',

  'terms.account.title': 'Ditt konto',
  'terms.account.li1': 'Ange en riktig e-postadress och håll dina inloggningsuppgifter för dig själv.',
  'terms.account.li2': 'Du ansvarar för det som sker via ditt konto.',
  'terms.account.li3': 'En person, ett konto. Dela inte din inbjudningskod.',

  'terms.content.title': 'Dina pass och din musik',
  'terms.content.p1':
    'Passen du gör är dina. Du ansvarar för att du har rätt att använda musiken i dem. Vi sparar i dina konton bara det du godkänner eller tillåtit i förväg med ditt engångssamtycke, och alltid som privat.',
  'terms.content.p2':
    'Assistenten kan göra fel: låtar, tider och ansträngning är förslag. Kontrollera ett pass innan du håller det.',

  'terms.use.title': 'Vad du inte får göra',
  'terms.use.li1': 'Försök inte förstöra, överbelasta eller kringgå tjänsten eller dess gränser.',
  'terms.use.li2':
    'Använd den inte för att bryta mot lagen eller mot villkoren hos MOWL, Spotify eller Swedebeat.',
  'terms.use.li3': 'Använd inte någon annans konto.',

  'terms.liability.title': 'Tillgänglighet och ansvar',
  'terms.liability.p1':
    'Vi gör vårt bästa för att hålla classbeat igång men lovar inte att den alltid är tillgänglig eller felfri. I den mån lagen tillåter ansvarar vi inte för förlust som beror på att du använt en betatjänst. Inget här begränsar rättigheter du har enligt lag som konsument.',

  'terms.ending.title': 'Avslut',
  'terms.ending.p1':
    'Du kan sluta när som helst genom att radera ditt konto. Vi kan stänga av ett konto som bryter mot villkoren eller skadar tjänsten. Vad vi gör med dina uppgifter beskrivs i {privacy}.',

  'terms.law.title': 'Ändringar och lag',
  'terms.law.p1':
    'Vi kan uppdatera villkoren och säger det i tjänsten innan en ändring som spelar roll för dig börjar gälla. Frågor går till {email}.',
  'terms.law.p2':
    'Svensk lag gäller för de här villkoren, utan att ta bort de tvingande konsumentskyddsreglerna i det EU- eller EES-land där du bor. Om du är konsument i EU eller EES kan du låta en tvist prövas av domstol i det land där du bor eller av svensk domstol, och vi kan väcka talan mot dig bara vid domstol i det land där du bor. Du kan också vända dig till din nationella konsumenttvistnämnd.',
}
