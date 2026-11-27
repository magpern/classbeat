import type { Catalogue } from './en'

// Dansk til lytteforhåndsvisningen på Spotify (se preview.en.ts).
export const previewDa: Catalogue = {
  'preview.title': 'Lyt på Spotify',
  'preview.intro':
    'Afspil klassens sange i din egen Spotify-app, og følg med i hvor du er. Spotify afspiller én sang ad gangen, så en overgang høres ikke her. Intet afspilles, før du trykker på Afspil.',
  'preview.loading': 'Tjekker lytteforhåndsvisningen',
  'preview.loadFailed': 'Lytteforhåndsvisningen kunne ikke indlæses. Prøv igen om lidt.',

  'preview.state.idle': 'Intet afspilles herfra. Tryk på Afspil ved en sang, eller klik på tidslinjen.',
  'preview.state.ended': 'Der afspilles ikke mere. Tryk på Afspil for at lytte igen.',
  'preview.state.starting': 'Starter i din Spotify-app.',
  'preview.state.playing': 'Afspiller på {device}.',
  'preview.state.paused': 'På pause på {device}.',
  'preview.state.external':
    'Noget andet afspilles i din Spotify-app, så der er ingen markør. Tryk på Afspil her for at lytte til holdet igen.',
  'preview.state.unaligned':
    'Sangens timing i forhold til Spotifys indspilning er ikke pålidelig, så markøren vises ikke. Den afspilles stadig.',
  'preview.state.outside_before':
    'Spotify afspiller delen før holdet starter (en intro), så der er ingen markør endnu.',
  'preview.state.outside_after':
    'Spotify afspiller forbi den del af sangen, som holdet bruger, så der er ingen markør.',
  'preview.state.seek': 'Positionen sprang (du eller Spotify sprang). Markøren følger med.',
  'preview.state.device': 'Afspiller nu på {device}. Markøren følger med.',
  'preview.state.device.none': 'din Spotify-app',

  'preview.cursor.none': 'Ingen markør.',
  'preview.cursor.at': 'Markør ved {time}, omkring ±{ms} ms.',
  'preview.cursor.overlap': 'Inde i en overlapning af to sange: markøren er kun omtrentlig, omkring ±{ms} ms.',
  'preview.timeline.label': 'Holdet sang for sang. Overlapninger er skraverede.',
  'preview.timeline.song': 'Sang {n}: {title}. Tryk på Enter for at afspille den fra start.',
  'preview.timeline.overlap': 'Overlapning af sang {a} og sang {b}',
  'preview.play': 'Afspil',
  'preview.playSong': 'Afspil sang {n}',
  'preview.pause': 'Pause',

  'preview.lane.title': 'To sange overlapper her. Hvilken vil du høre?',
  'preview.lane.incoming': 'Den indgående sang ({title})',
  'preview.lane.outgoing': 'Den udgående sang ({title})',
  'preview.lane.cancel': 'Annuller',

  'preview.action.reconnect': 'Tilslut Spotify igen',
  'preview.action.open_spotify': 'Åbn Spotify, og tryk derefter på Afspil igen.',
  'preview.action.sign_in': 'Log ind igen',
  'preview.action.reload': 'Genindlæs siden',

  'preview.err.not_connected': 'Spotify er ikke tilsluttet. Tilslut det for at lytte her.',
  'preview.err.needs_reconnect': 'Spotify skal tilsluttes igen, før det kan afspille her.',
  'preview.err.preview_not_granted':
    'Lytteforhåndsvisningen er ikke slået til for din Spotify-forbindelse. Tilslut Spotify igen, og tillad afspilningskontrol.',
  'preview.err.premium_or_allowlist':
    'Spotify afviste at afspille. Afspilningskontrol kræver Spotify Premium, og så længe din Spotify-app er i udviklingstilstand, skal dit navn og din e-mail stå under dens brugeradministration.',
  'preview.err.no_device':
    'Der er ingen åben Spotify-app at afspille på. Åbn Spotify på din telefon eller computer, og tryk derefter på Afspil igen.',
  'preview.err.rate_limited': 'Spotify beder dig vente lidt før næste anmodning. Prøv igen om lidt.',
  'preview.err.rate_limited_for': 'Spotify beder dig vente. Prøv igen om {seconds} sekunder.',
  'preview.err.upstream_unavailable': 'Spotify svarer ikke lige nu. Prøv igen om lidt.',
  'preview.err.invalid_response': 'Spotify gav et svar, der ikke kunne bruges. Prøv igen om lidt.',
  'preview.err.network': 'Forbindelsen blev afbrudt. Tjek din internetforbindelse, og prøv igen.',
  'preview.err.unauthorized': 'Du blev logget ud. Log ind igen for at lytte.',
  'preview.err.no_draft': 'Holdet har ingen version med Spotify-sange at lytte til. Genindlæs siden.',
  'preview.err.not_found': 'Holdet blev ikke fundet.',
  'preview.err.out_of_range': 'Den position ligger uden for sangen.',
  'preview.err.stale_base': 'Holdet blev ændret, mens siden var åben. Genindlæs siden, og prøv igen.',
  'preview.err.no_analysis': 'Sangen er ikke analyseret, så dens timing kan ikke justeres.',
  'preview.err.correction_limit': 'Der er for mange timingjusteringer for dette hold.',
  'preview.err.invalid_input': 'Den justering er ikke tilladt. Den må højst være et minut til hver side.',
  'preview.err.generic': 'Noget gik galt. Prøv igen om lidt.',

  'preview.corr.title': 'Finjuster timingen for sang {n}',
  'preview.corr.intro':
    'Hvis markøren ligger foran musikken, du hører, så tryk på +. Ligger den bagefter, så tryk på −. En justering flytter kun markøren her: holdet ændres ikke.',
  'preview.corr.pick': 'Sang',
  'preview.corr.value': 'Din justering: {value} s',
  'preview.corr.none': 'Ingen justering.',
  'preview.corr.step': 'Flyt {delta} s',
  'preview.corr.listenAgain':
    'Lyt igen: Denne justering blev lavet, før musikken blev analyseret igen. Den vises her, men bruges ikke, før du bekræfter den.',
  'preview.corr.confirm': 'Bekræft justeringen',
  'preview.corr.clear': 'Fjern justeringen',
  'preview.corr.saving': 'Gemmer',
  'preview.corr.saved': 'Gemt.',
  'preview.prov.title': 'Hvor timingen for sang {n} kommer fra',
  'preview.prov.note': 'spotdowns analyse og din justering holdes adskilt og lægges aldrig sammen til ét tal.',
}
