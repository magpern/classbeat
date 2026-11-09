import type { Catalogue } from './en'

// Swedish text of the help and contact page (see help.en.ts).
export const helpSv: Catalogue = {
  'help.link': 'Hjälp',
  'help.inline.account': 'kontosidan',
  'help.title': 'Hjälp och kontakt',
  'help.lead': 'Korta svar, och hur du når oss. Du behöver inte logga in för att läsa den här sidan.',

  'help.connect.title': 'Koppla dina konton',
  'help.connect.mowl.q': 'Hur kopplar jag MOWL?',
  'help.connect.mowl.a':
    'Öppna fliken Anslutningar på {account} och tryck på Anslut vid MOWL. Logga in på ditt Intelligent Cycling-konto (MOWL) med e-postadress och lösenord. På en större skärm kan du också få en kod att skanna med MOWL-appen. Vi använder lösenordet en enda gång för att få en inloggningstoken och sparar bara token, krypterad. Du kan koppla från när som helst, och då raderar vi den. Vill du avsluta inloggningen hos MOWL byter du ditt MOWL-lösenord.',
  'help.connect.spotify.q': 'Hur kopplar jag Spotify?',
  'help.connect.spotify.a':
    'Spotify behövs när du vill att vi ska välja låtar åt dig. På fliken Anslutningar trycker du på Anslut vid Spotify. Vi guidar dig genom att skapa en egen Spotify-app i Spotify Developer Dashboard, så att begränsningarna blir dina egna. Sedan klistrar du in appens Client ID här. Den som äger appen behöver Spotify Premium. Därefter loggar du in hos Spotify och ger tillåtelse. Vi behöver aldrig appens Client Secret och frågar aldrig efter den.',
  'help.connect.swedebeat.q': 'Behöver jag Swedebeat?',
  'help.connect.swedebeat.a':
    'Nej, det är valfritt. Undervisar du med Swedebeat trycker du på Anslut vid Swedebeat på fliken Anslutningar. Då kan du utforma ett pass till en av dina Swedebeat-mixar, som hämtas från ditt MOWL-konto, och matcha passets låtar mot Swedebeat. Precis som med MOWL använder vi lösenordet en enda gång och sparar bara token.',

  'help.design.title': 'Utforma ett pass',
  'help.design.how.q': 'Hur utformas ett pass?',
  'help.design.how.a':
    'Du beskriver längd, ansträngning och musik: din egen spellista, låtar som vi väljer åt dig, en Swedebeat-mix eller ingen musik. En AI-assistent föreslår intervaller, ansträngningsnivåer och instruktioner. Om du bad om låtar föreslår den också en låtlista som kontrolleras mot MOWL:s tempo och längd. Det kan ta några minuter, och du kan lämna sidan under tiden. Resultatet är ett förslag, så kontrollera passet innan du håller det.',
  'help.design.saved.q': 'Vad sparas, och när?',
  'help.design.saved.a':
    'Assistenten föreslår bara. När du godkänner en låtlista skapar vi en privat spellista med de låtarna i din Spotify och lägger till den i din MOWL-musik. Själva passet hamnar i ditt MOWL-konto, privat, först när du trycker på Spara i MOWL på passet. Godkänner du inte en lista i tid skrivs ingenting till din Spotify.',
  'help.design.change.q': 'Kan jag ändra ett pass efteråt?',
  'help.design.change.a':
    'Ja. Beskriv vad som ska vara annorlunda på passets sida, eller använd Byt låtar för att byta ut några av låtarna. Varje ändring blir en ny version. Tidigare versioner finns kvar, och du kan göra en av dem aktuell med Använd den här versionen. Spara i MOWL igen för att uppdatera passet där.',

  'help.plan.title': 'Planer och gränser',
  'help.plan.limits.q': 'Vilka gränser gäller för mig?',
  'help.plan.limits.a':
    'Din plan avgör hur många pass du kan utforma varje månad och hur många ändringar ett pass kan få. Den avgör också hur långt ett pass får vara, hur många utformningar som kan pågå samtidigt och om spel och lopp ingår. Dessutom finns en månadsbudget för assistentens arbete. Månader räknas i UTC och börjar om den första. Fliken Plan och användning på kontosidan visar vad du har använt.',
  'help.plan.code.q': 'Hur använder jag en kod eller provperiod?',
  'help.plan.code.a':
    'På fliken Plan och användning skriver du koden under Lös in en kod, eller trycker på knappen för provperioden när en sådan erbjuds. En kod kan vara ogiltig, ha gått ut eller vara förbrukad, och en provperiod kan användas en gång.',

  'help.signin.title': 'Konto och inloggning',
  'help.signin.reset.q': 'Jag har glömt mitt lösenord',
  'help.signin.reset.a':
    'Välj Har du glömt ditt lösenord? på inloggningssidan så skickar vi en länk som gäller i 30 minuter. Ett lösenord är 12 till 128 tecken långt. När du väljer ett nytt loggas du ut överallt.',
  'help.signin.methods.q': 'Logga in med Google, GitHub eller Microsoft?',
  'help.signin.methods.a':
    'Ja, när de erbjuds på inloggningssidan. Du kan koppla eller ta bort dem under Inloggning och säkerhet på kontosidan. Kontot använder den e-postadress som leverantören har verifierat.',
  'help.signin.twofactor.q': 'Finns det tvåfaktorsautentisering?',
  'help.signin.twofactor.a':
    'Ja. Under Inloggning och säkerhet kan du konfigurera en autentiseringsapp. Den ber då om en kod innan känsliga ändringar på den sidan. Du får återställningskoder som bara visas en gång, så spara dem.',
  'help.signin.devices.q': 'Var är jag inloggad?',
  'help.signin.devices.a':
    'Under Inloggning och säkerhet ser du de enheter du är inloggad på och säkerhetshändelserna de senaste 30 dagarna. Du kan logga ut en enhet eller logga ut överallt. Ser du något du inte själv har gjort ska du logga ut överallt och byta lösenord.',

  'help.data.title': 'Dina data och att radera kontot',
  'help.data.export.q': 'Kan jag få en kopia av mina data?',
  'help.data.export.a':
    'Ja. På fliken Dina data ger Ladda ner data dig en fil med ditt konto, dina pass, låtlistor, din användning och din inloggningshistorik.',
  'help.data.delete.q': 'Hur raderar jag mitt konto?',
  'help.data.delete.a':
    'På fliken Dina data trycker du på Radera mitt konto och skriver din e-postadress för att bekräfta. Då raderas ditt konto, dina pass och låtlistor, din plan och användning, dina anslutningar och din inloggningshistorik. Pågående jobb stoppas först. Det går inte att ångra. Pass som sparats i MOWL och spellistor i din Spotify finns kvar där, så radera dem i de tjänsterna om du inte vill ha dem. Mer i {privacy}.',

  'help.contact.title': 'Kontakt',
  'help.contact.p1': 'Skriv till oss på {email}.',
  'help.contact.include': 'Ta gärna med:',
  'help.contact.li1': 'E-postadressen till ditt classbeat-konto.',
  'help.contact.li2': 'Vad du gjorde och vad du förväntade dig skulle hända.',
  'help.contact.li3': 'Tidpunkten, och felmeddelandet om du såg något.',
  'help.contact.never':
    'Skicka aldrig ett lösenord, en inloggningstoken, en återställningskod eller en engångskod. Vi behöver dem aldrig.',
  'help.contact.p2':
    'Vill du se, rätta, exportera eller radera dina data, läs {privacy}. Det mesta kan du göra själv på kontosidan.',
  'help.contact.none': 'Ingen supportadress är tillgänglig just nu. Försök igen senare.',
}
