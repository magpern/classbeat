import type { Catalogue } from './en'

// Swedish text of the public pages How it works and Features and of their links (see public.en.ts, PP7).
// The availability notes keep the meaning of the English ones; a song-picking note names Spotify, the
// Client ID and the plan (publicSite/songPickingScan.ts).
export const publicSv: Catalogue = {
  'meta.howItWorks.title': 'Så fungerar det | classbeat',
  'meta.howItWorks.description':
    'Beskriv ett pass, finslipa det i chatten, godkänn det och spara det i MOWL. Se vad du ansluter.',
  'meta.features.title': 'Funktioner | classbeat',
  'meta.features.description':
    'Vad classbeat gör för instruktörer inom inomhuscykling, och vad varje funktion kräver: en anslutning, din plan eller aktivering.',

  'pub.nav.label': 'Sidor',
  'pub.nav.features': 'Funktioner',
  'pub.cta.line': 'Du registrerar dig med en inbjudan.',

  'claims.class.available': 'Tillgänglig',
  'claims.class.connection': 'Kräver en anslutning',
  'claims.class.gated': 'Beror på plan eller aktivering',
  'claims.note.available': 'Ingår i ditt konto.',
  'claims.note.mowl': 'Kräver att ditt MOWL-konto är anslutet.',
  'claims.note.songPicking':
    'Kräver en Spotify-anslutning med ditt eget Client ID, och att låtval ingår i din plan. Tillgängligt när vi har aktiverat funktionen.',
  'claims.note.swedebeat':
    'Kräver att ditt Swedebeat-konto är anslutet. Tillgängligt när vi har aktiverat funktionen.',
  'claims.note.games':
    'Kräver en plan som innehåller spel. MOWL-spel kräver en kommersiell MOWL-licens, som vi inte kontrollerar.',
  'claims.note.switch':
    'Kräver att ditt MOWL-konto är anslutet. Tillgängligt när vi har aktiverat funktionen.',
  'claims.note.plan':
    'Bestäms av din plan. Fliken Plan och användning på din kontosida visar vad du har använt.',

  'how.title': 'Så fungerar classbeat',
  'how.lead':
    'Beskriv passet du vill hålla. Vi utformar det, du finslipar det, och inget träningspass når MOWL förrän du godkänner det.',
  'how.steps.title': 'Från begäran till träningspass',
  'how.step.describe.title': 'Beskriv',
  'how.step.describe.text':
    'Skriv passet med egna ord: längd, ansträngning, klättringar, sprintar, ett spel, musiken. Det du utelämnar bestämmer vi, och vi visar vad vi har antagit.',
  'how.step.generate.title': 'Utforma',
  'how.step.generate.text':
    'Vi utformar passet från uppvärmning till nedvarvning och ritar upp det som en profil. Det kan ta några minuter, och du kan lämna sidan under tiden.',
  'how.step.refine.title': 'Finslipa',
  'how.step.refine.text':
    'Be om ändringar i chatten, till exempel en längre nedvarvning eller en lättare andra klättring. Varje ändring blir en ny version, och tidigare versioner finns kvar.',
  'how.step.save.title': 'Godkänn och spara',
  'how.step.save.text':
    'När du är nöjd trycker du på Spara i MOWL. Passet blir ett privat träningspass i ditt MOWL-konto. Innan dess skrivs inget träningspass till MOWL.',
  'how.connect.title': 'Det du ansluter',
  'how.connect.lead':
    'Du ansluter konton på din kontosida. Vi behåller aldrig ditt lösenord till MOWL eller Swedebeat.',
  'how.connect.mowl.name': 'MOWL',
  'how.connect.mowl.text': 'Krävs. Vi läser din musik därifrån och sparar dina pass där.',
  'how.connect.spotify.name': 'Spotify',
  'how.connect.spotify.text':
    'Valfritt. Bara om du vill att vi ska välja låtar åt dig. Du skapar en egen Spotify-app, så gränserna är dina. Dess ägare behöver Spotify Premium.',
  'how.connect.swedebeat.name': 'Swedebeat',
  'how.connect.swedebeat.text':
    'Valfritt. Bara om du håller pass med Swedebeat, för att utforma ett pass till en av dina mixar.',

  'feat.title': 'Det här kan du göra med classbeat',
  'feat.lead': 'Varje funktion säger vad den kräver: ditt konto, en anslutning eller rätt plan eller aktivering.',

  'feat.group.request': 'Beskriv det på ditt sätt',
  'feat.group.music': 'Musik som passar passet',
  'feat.group.shape': 'Forma passet',
  'feat.group.control': 'Behåll kontrollen',

  'feat.describe.title': 'Skriv passet med egna ord',
  'feat.describe.text': 'Säg det som är viktigt och hoppa över resten. Vi fyller i luckorna.',
  'feat.assumptions.title': 'Se hur vi tolkar din begäran',
  'feat.assumptions.text':
    'Varje punkt är markerad som begärd av dig, antagen av oss eller inte möjlig som du bad om.',
  'feat.advanced.title': 'Ställ in varje detalj själv',
  'feat.advanced.text': 'Föredrar du ett formulär? Med Avancerat väljer du varje detalj.',

  'feat.playlist.title': 'Utforma till din egen spellista',
  'feat.playlist.text': 'Välj en spellista från din MOWL-musik, så tidsätts passet efter dess låtar.',
  'feat.songs.title': 'Låt oss välja låtarna',
  'feat.songs.text':
    'Be om låtar och godkänn listan först. De låtar du godkänner hamnar i en privat spellista i din Spotify.',
  'feat.swedebeat.title': 'Utforma till en Swedebeat-mix',
  'feat.swedebeat.text': 'Tidsätt passet efter en av dina Swedebeat-mixar.',
  'feat.noMusic.title': 'Pass utan musik',
  'feat.noMusic.text': 'Få intervaller och instruktioner, och spela din egen musik.',

  'feat.profile.title': 'Se passet innan du håller det',
  'feat.profile.text': 'Passet ritas upp som en profil av zoner, med segment, längd och TSS.',
  'feat.chat.title': 'Ändra det genom att chatta',
  'feat.chat.text': 'Säg vad som ska vara annorlunda. Varje ändring blir en ny version.',
  'feat.versions.title': 'Jämför och gå tillbaka till versioner',
  'feat.versions.text':
    'Jämför två versioner, gör en tidigare version aktuell eller duplicera ett pass för att prova en variant.',
  'feat.games.title': 'Lägg till ett MOWL-spel',
  'feat.games.text': 'Be om Racetrack, Power Pull, Fast Lane eller Overflow i ett cykelpass.',
  'feat.running.title': 'Håll löppass också',
  'feat.running.text':
    'Utforma ett löppass till en spellista från din MOWL-musik. Löppass har inga spel.',

  'feat.save.title': 'Spara i MOWL när du bestämmer',
  'feat.save.text':
    'Tryck på Spara i MOWL så blir passet ett privat träningspass i ditt konto. Uppdatera det senare från samma sida.',
  'feat.limits.title': 'Gränser som passar din plan',
  'feat.limits.text':
    'Din plan bestämmer hur många pass du utformar varje månad, hur många ändringar ett pass kan ha och hur långt ett pass kan vara.',
  'feat.data.title': 'Dina data förblir dina',
  'feat.data.text': 'Exportera dina data eller radera ditt konto från din kontosida.',
  'feat.languages.title': 'Använd det på ditt språk',
  'feat.languages.text': 'Webbplatsen finns på engelska, svenska och danska. Passen skrivs på engelska.',
}
