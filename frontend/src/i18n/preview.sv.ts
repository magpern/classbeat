import type { Catalogue } from './en'

// Svenska för lyssningsförhandsvisningen på Spotify (se preview.en.ts).
export const previewSv: Catalogue = {
  'preview.title': 'Lyssna på Spotify',
  'preview.intro':
    'Spela passets låtar i din egen Spotify-app och följ var du är. Spotify spelar en låt i taget, så en övertoning hörs inte här. Inget spelas förrän du trycker på Spela.',
  'preview.loading': 'Kontrollerar lyssningsförhandsvisningen',
  'preview.loadFailed': 'Lyssningsförhandsvisningen gick inte att läsa in. Försök igen om en stund.',

  'preview.state.idle': 'Inget spelas härifrån. Tryck på Spela vid en låt eller klicka på tidslinjen.',
  'preview.state.ended': 'Inget spelas längre. Tryck på Spela för att lyssna igen.',
  'preview.state.starting': 'Startar i din Spotify-app.',
  'preview.state.playing': 'Spelar på {device}.',
  'preview.state.paused': 'Pausad på {device}.',
  'preview.state.external':
    'Något annat spelas i din Spotify-app, så det finns ingen markör. Tryck på Spela här för att lyssna på passet igen.',
  'preview.state.unaligned':
    'Låtens tid mot Spotifys inspelning är inte pålitlig, så markören visas inte. Den spelas ändå.',
  'preview.state.outside_before':
    'Spotify spelar delen före passets start (ett intro), så det finns ingen markör än.',
  'preview.state.outside_after':
    'Spotify spelar förbi den del av låten som passet använder, så det finns ingen markör.',
  'preview.state.seek': 'Positionen hoppade (du eller Spotify hoppade). Markören följer med.',
  'preview.state.device': 'Spelar nu på {device}. Markören följer med.',
  'preview.state.device.none': 'din Spotify-app',

  'preview.cursor.none': 'Ingen markör.',
  'preview.cursor.at': 'Markör vid {time}, ungefär ±{ms} ms.',
  'preview.cursor.overlap': 'Inom en överlappning av två låtar: markören är bara ungefärlig, cirka ±{ms} ms.',
  'preview.timeline.label': 'Passet låt för låt. Överlappningar är streckade.',
  'preview.timeline.song': 'Låt {n}: {title}. Tryck på Retur för att spela den från början.',
  'preview.timeline.overlap': 'Överlappning av låt {a} och låt {b}',
  'preview.play': 'Spela',
  'preview.playSong': 'Spela låt {n}',
  'preview.pause': 'Pausa',

  'preview.lane.title': 'Två låtar överlappar här. Vilken vill du höra?',
  'preview.lane.incoming': 'Den inkommande låten ({title})',
  'preview.lane.outgoing': 'Den utgående låten ({title})',
  'preview.lane.cancel': 'Avbryt',

  'preview.action.reconnect': 'Anslut Spotify igen',
  'preview.action.open_spotify': 'Öppna Spotify och tryck sedan på Spela igen.',
  'preview.action.sign_in': 'Logga in igen',
  'preview.action.reload': 'Ladda om sidan',

  'preview.err.not_connected': 'Spotify är inte anslutet. Anslut det för att lyssna här.',
  'preview.err.needs_reconnect': 'Spotify måste anslutas på nytt innan det kan spela här.',
  'preview.err.preview_not_granted':
    'Lyssningsförhandsvisningen är inte påslagen för din Spotify-anslutning. Anslut Spotify igen och tillåt uppspelningskontroll.',
  'preview.err.premium_or_allowlist':
    'Spotify vägrade spela. Uppspelningskontroll kräver Spotify Premium, och så länge din Spotify-app är i utvecklingsläge måste ditt namn och din e-postadress finnas under dess användarhantering.',
  'preview.err.no_device':
    'Ingen Spotify-app är öppen att spela på. Öppna Spotify på mobilen eller datorn och tryck sedan på Spela igen.',
  'preview.err.rate_limited': 'Spotify ber dig vänta en stund före nästa förfrågan. Försök igen om en stund.',
  'preview.err.rate_limited_for': 'Spotify ber dig vänta. Försök igen om {seconds} sekunder.',
  'preview.err.upstream_unavailable': 'Spotify svarar inte just nu. Försök igen om en stund.',
  'preview.err.invalid_response': 'Spotify gav ett svar som inte gick att använda. Försök igen om en stund.',
  'preview.err.network': 'Anslutningen bröts. Kontrollera din internetanslutning och försök igen.',
  'preview.err.unauthorized': 'Du blev utloggad. Logga in igen för att lyssna.',
  'preview.err.no_draft': 'Passet har ingen version med Spotify-låtar att lyssna på. Ladda om sidan.',
  'preview.err.not_found': 'Passet hittades inte.',
  'preview.err.out_of_range': 'Den positionen ligger utanför låten.',
  'preview.err.stale_base': 'Passet ändrades medan sidan var öppen. Ladda om sidan och försök igen.',
  'preview.err.no_analysis': 'Låten har inte analyserats, så dess tid går inte att justera.',
  'preview.err.correction_limit': 'Det finns för många tidsjusteringar för det här passet.',
  'preview.err.invalid_input': 'Den justeringen är inte tillåten. Den får vara högst en minut åt vardera hållet.',
  'preview.err.generic': 'Något gick fel. Försök igen om en stund.',

  'preview.corr.title': 'Finjustera tiden för låt {n}',
  'preview.corr.intro':
    'Om markören ligger före musiken du hör, tryck på +. Ligger den efter, tryck på −. En justering flyttar bara markören här: passet ändras inte.',
  'preview.corr.pick': 'Låt',
  'preview.corr.value': 'Din justering: {value} s',
  'preview.corr.none': 'Ingen justering.',
  'preview.corr.step': 'Flytta {delta} s',
  'preview.corr.listenAgain':
    'Lyssna igen: den här justeringen gjordes innan musiken analyserades på nytt. Den visas här men används inte förrän du bekräftar den.',
  'preview.corr.confirm': 'Bekräfta justeringen',
  'preview.corr.clear': 'Ta bort justeringen',
  'preview.corr.saving': 'Sparar',
  'preview.corr.saved': 'Sparat.',
  'preview.prov.title': 'Varifrån tiden för låt {n} kommer',
  'preview.prov.note': 'classbeats analys och din justering hålls isär och läggs aldrig ihop till ett tal.',
}
