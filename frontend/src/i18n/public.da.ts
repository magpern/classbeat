import type { Catalogue } from './en'

// Danish text of the public pages How it works and Features and of their links (see public.en.ts, PP7).
// The availability notes keep the meaning of the English ones; a song-picking note names Spotify, the
// Client ID and the plan (publicSite/songPickingScan.ts).
export const publicDa: Catalogue = {
  'meta.howItWorks.title': 'Sådan virker det | classbeat',
  'meta.howItWorks.description':
    'Beskriv et hold, finjuster det i chatten, godkend det og gem det i MOWL. Se, hvad du tilslutter.',
  'meta.features.title': 'Funktioner | classbeat',
  'meta.features.description':
    'Hvad classbeat gør for instruktører i indendørscykling, og hvad hver funktion kræver: en forbindelse, din plan eller aktivering.',

  'pub.nav.label': 'Sider',
  'pub.nav.features': 'Funktioner',
  'pub.cta.line': 'Du opretter dig med en invitation.',

  'claims.class.available': 'Tilgængelig',
  'claims.class.connection': 'Kræver en forbindelse',
  'claims.class.gated': 'Afhænger af plan eller aktivering',
  'claims.note.available': 'Indgår i din konto.',
  'claims.note.mowl': 'Kræver, at din MOWL-konto er tilsluttet.',
  'claims.note.songPicking':
    'Kræver en Spotify-forbindelse med dit eget Client ID, og at sangvalg indgår i din plan. Tilgængeligt, når vi har aktiveret funktionen.',
  'claims.note.swedebeat':
    'Kræver, at din Swedebeat-konto er tilsluttet. Tilgængeligt, når vi har aktiveret funktionen.',
  'claims.note.games':
    'Kræver en plan, der indeholder spil. MOWL-spil kræver en kommerciel MOWL-licens, som vi ikke kontrollerer.',
  'claims.note.switch':
    'Kræver, at din MOWL-konto er tilsluttet. Tilgængeligt, når vi har aktiveret funktionen.',
  'claims.note.plan':
    'Bestemmes af din plan. Fanen Plan og forbrug på din kontoside viser, hvad du har brugt.',

  'how.title': 'Sådan virker classbeat',
  'how.lead':
    'Beskriv det hold, du vil undervise i. Vi designer det, du finjusterer det, og intet træningspas når MOWL, før du godkender det.',
  'how.steps.title': 'Fra anmodning til træningspas',
  'how.step.describe.title': 'Beskriv',
  'how.step.describe.text':
    'Skriv holdet med dine egne ord: længde, anstrengelse, stigninger, sprints, et spil, musikken. Det, du udelader, bestemmer vi, og vi viser, hvad vi har antaget.',
  'how.step.generate.title': 'Design',
  'how.step.generate.text':
    'Vi designer holdet fra opvarmning til nedkøling og tegner det som en profil. Det kan tage nogle minutter, og du kan forlade siden imens.',
  'how.step.refine.title': 'Finjuster',
  'how.step.refine.text':
    'Bed om ændringer i chatten, for eksempel en længere nedkøling eller en lettere anden stigning. Hver ændring giver en ny version, og tidligere versioner bliver.',
  'how.step.save.title': 'Godkend og gem',
  'how.step.save.text':
    'Når du er tilfreds, trykker du på Gem i MOWL. Holdet bliver et privat træningspas på din MOWL-konto. Indtil da bliver der ikke skrevet noget træningspas til MOWL.',
  'how.connect.title': 'Det, du tilslutter',
  'how.connect.lead':
    'Du tilslutter konti på din kontoside. Vi beholder aldrig din adgangskode til MOWL eller Swedebeat.',
  'how.connect.mowl.name': 'MOWL',
  'how.connect.mowl.text': 'Påkrævet. Vi læser din musik derfra og gemmer dine hold dér.',
  'how.connect.spotify.name': 'Spotify',
  'how.connect.spotify.text':
    'Valgfrit. Kun hvis du vil have, at vi vælger sange til dig. Du laver en Spotify-app til dig selv, så grænserne er dine. Dens ejer skal have Spotify Premium.',
  'how.connect.swedebeat.name': 'Swedebeat',
  'how.connect.swedebeat.text':
    'Valgfrit. Kun hvis du underviser med Swedebeat, for at designe et hold til et af dine mix.',

  'feat.title': 'Det kan du med classbeat',
  'feat.lead': 'Hver funktion siger, hvad den kræver: din konto, en forbindelse eller den rette plan eller aktivering.',

  'feat.group.request': 'Beskriv det på din måde',
  'feat.group.music': 'Musik, der passer til holdet',
  'feat.group.shape': 'Form holdet',
  'feat.group.control': 'Behold kontrollen',

  'feat.describe.title': 'Skriv holdet med dine egne ord',
  'feat.describe.text': 'Sig det, der er vigtigt, og lad resten ligge. Vi udfylder hullerne.',
  'feat.assumptions.title': 'Se, hvordan vi læser din anmodning',
  'feat.assumptions.text':
    'Hvert punkt er markeret som bedt om af dig, antaget af os eller ikke muligt som bedt om.',
  'feat.advanced.title': 'Indstil hver detalje selv',
  'feat.advanced.text': 'Foretrækker du en formular? Med Avanceret vælger du hver detalje.',

  'feat.playlist.title': 'Design til din egen playliste',
  'feat.playlist.text': 'Vælg en playliste fra din MOWL-musik, så afstemmes holdet efter dens sange.',
  'feat.songs.title': 'Lad os vælge sangene',
  'feat.songs.text':
    'Bed om sange, og godkend listen først. De sange, du godkender, kommer i en privat playliste i din Spotify.',
  'feat.swedebeat.title': 'Design til et Swedebeat-mix',
  'feat.swedebeat.text': 'Afstem holdet efter et af dine Swedebeat-mix.',
  'feat.noMusic.title': 'Hold uden musik',
  'feat.noMusic.text': 'Få intervaller og instruktioner, og spil din egen musik.',

  'feat.profile.title': 'Se holdet, før du underviser',
  'feat.profile.text': 'Holdet tegnes som en profil af zoner, med segmenter, længde og TSS.',
  'feat.chat.title': 'Ændr det ved at chatte',
  'feat.chat.text': 'Sig, hvad der skal være anderledes. Hver ændring giver en ny version.',
  'feat.versions.title': 'Sammenlign og gå tilbage til versioner',
  'feat.versions.text':
    'Sammenlign to versioner, gør en tidligere version gældende, eller dupliker et hold for at prøve en variant.',
  'feat.games.title': 'Tilføj et MOWL-spil',
  'feat.games.text': 'Bed om Racetrack, Power Pull, Fast Lane eller Overflow i et cyklinghold.',
  'feat.running.title': 'Lav også løbehold',
  'feat.running.text':
    'Design et løbehold til en playliste fra din MOWL-musik. Løbehold har ingen spil.',

  'feat.save.title': 'Gem i MOWL, når du bestemmer',
  'feat.save.text':
    'Tryk på Gem i MOWL, så bliver holdet et privat træningspas på din konto. Opdater det senere fra samme side.',
  'feat.limits.title': 'Grænser, der passer til din plan',
  'feat.limits.text':
    'Din plan bestemmer, hvor mange hold du designer hver måned, hvor mange ændringer et hold kan have, og hvor langt et hold kan være.',
  'feat.data.title': 'Dine data forbliver dine',
  'feat.data.text': 'Eksportér dine data, eller slet din konto, fra din kontoside.',
  'feat.languages.title': 'Brug det på dit sprog',
  'feat.languages.text': 'Siden findes på engelsk, svensk og dansk. Holdene skrives på engelsk.',
  'meta.pricing.title': 'Planer | classbeat',
  'meta.pricing.description':
    'Hvad hver classbeat-plan indeholder: hold om måneden, holdlængde, spil, FTP-test og designkvalitet. Planerne er ikke åbne endnu.',
  'pricing.title': 'Planer',
  'pricing.lead':
    'Tre planer, fra en lille gratis kvote til mange hold. Planerne er ikke åbne for køb endnu.',
  'pricing.plans.title': 'Hvad hver plan indeholder',
  'pricing.free.blurb': 'Prøv classbeat med en lille kvote hver måned.',
  'pricing.instructor.blurb': 'Til undervisere, der holder hold hver uge.',
  'pricing.pro.blurb': 'Til undervisere, der designer mange hold.',
  'pricing.quality.note': 'Better og Best giver mere omhyggelige designs.',
  'pricing.same.title': 'Det samme på alle planer',
  'pricing.same.races': 'Race, de korte, hårde indsatser.',
  'pricing.same.classes': 'At køre hold, din holdhistorik samt eksport og sletning af dine data.',
  'pricing.same.connections': 'Dine forbindelser til MOWL, Spotify og Swedebeat.',
  'pricing.same.songs':
    'Sangvalget er det samme på alle planer og kræver en Spotify-forbindelse med dit eget Client ID, når vi tænder for det.',
}
