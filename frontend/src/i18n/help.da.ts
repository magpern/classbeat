import type { Catalogue } from './en'

// Danish text of the help and contact page (see help.en.ts).
export const helpDa: Catalogue = {
  'help.link': 'Hjælp',
  'help.inline.account': 'kontosiden',
  'help.title': 'Hjælp og kontakt',
  'help.lead':
    'Korte svar om, hvordan classbeat fungerer, og hvordan du kommer i kontakt med os. Du behøver ikke at være logget ind for at læse denne side.',

  'help.connect.title': 'Forbind dine konti',
  'help.connect.mowl.q': 'Hvordan forbinder jeg MOWL?',
  'help.connect.mowl.a':
    'Åbn fanen Forbindelser på {account}. Log ind på din Intelligent Cycling-konto (MOWL) med e-mailadresse og adgangskode, eller scan koden, der vises, med MOWL-appen på din telefon. classbeat bruger adgangskoden én gang til at få en login-token og gemmer kun token, krypteret, aldrig adgangskoden. Du kan afbryde forbindelsen når som helst, og så slettes den token, classbeat har. Vil du afslutte loginet hos MOWL, skal du skifte din MOWL-adgangskode.',
  'help.connect.spotify.q': 'Hvordan forbinder jeg Spotify?',
  'help.connect.spotify.a':
    'Spotify skal bruges, når du lader classbeat vælge sange til dig. På fanen Forbindelser fører Opsæt Spotify dig igennem at oprette din egen Spotify-app i Spotify Developer Dashboard, så begrænsningerne bliver dine egne (den, der ejer appen, skal have Spotify Premium), og at indsætte appens Client ID i classbeat. Derefter logger du ind hos Spotify og giver classbeat lov. classbeat har aldrig brug for appens Client Secret og spørger aldrig efter den.',
  'help.connect.swedebeat.q': 'Har jeg brug for Swedebeat?',
  'help.connect.swedebeat.a':
    'Nej, det er valgfrit. Når du laver et hold til et af dine Swedebeat-mix, bruges de mix, der står på din MOWL-konto. Underviser du med Swedebeat, så slå Jeg bruger Swedebeat til på fanen Forbindelser og log ind med din Swedebeat-konto, så kan classbeat matche holdets sange med Swedebeat. Som med MOWL bruges adgangskoden kun én gang, og kun token gemmes.',

  'help.design.title': 'Lav et hold',
  'help.design.how.q': 'Hvordan laves et hold?',
  'help.design.how.a':
    'Du beskriver længde, anstrengelse og musik: din egen playliste, sange som classbeat vælger til dig, et Swedebeat-mix eller ingen musik. En AI-assistent foreslår intervaller, anstrengelsesniveauer og instruktioner og, hvis du bad om sange, en sangliste, der kontrolleres mod MOWL’s tempo og længde. Det kan tage nogle minutter, og du kan forlade siden imens. Resultatet er et forslag, så tjek holdet, før du underviser.',
  'help.design.saved.q': 'Hvad bliver gemt, og hvornår?',
  'help.design.saved.a':
    'Assistenten foreslår kun. Når du godkender en sangliste, opretter classbeat en privat playliste med de sange i din Spotify og tilføjer den til din MOWL-musik. Selve holdet kommer først på din MOWL-konto, privat, når du trykker på Gem i MOWL på holdet. Godkender du ikke en liste i tide, bliver der ikke skrevet noget til din Spotify.',
  'help.design.change.q': 'Kan jeg ændre et hold bagefter?',
  'help.design.change.a':
    'Ja. Beskriv, hvad der skal være anderledes, på holdets side, eller brug Skift sange til at skifte nogle af sangene ud. Hver ændring giver en ny version. Tidligere versioner bliver, og du kan gøre en af dem gældende med Brug denne version. Gem i MOWL igen for at opdatere holdet dér.',

  'help.plan.title': 'Planer og grænser',
  'help.plan.limits.q': 'Hvilke grænser gælder for mig?',
  'help.plan.limits.a':
    'Din plan bestemmer, hvor mange hold du kan lave hver måned, hvor mange ændringer et hold kan få, hvor langt et hold må være, hvor mange der kan laves på samme tid, og om spil og løb er med. Derudover er der et månedligt budget til assistentens arbejde. Måneder regnes i UTC og begynder forfra den første. Fanen Plan og forbrug på kontosiden viser, hvad du har brugt.',
  'help.plan.code.q': 'Hvordan indløser jeg en kode eller starter en prøveperiode?',
  'help.plan.code.a':
    'På fanen Plan og forbrug skriver du koden under Indløs en kode, eller du trykker på knappen til prøveperioden, når den tilbydes. En kode kan være ugyldig, udløbet eller opbrugt, og en prøveperiode kan bruges én gang.',

  'help.signin.title': 'Konto og login',
  'help.signin.reset.q': 'Jeg har glemt min adgangskode',
  'help.signin.reset.a':
    'Vælg Har du glemt din adgangskode? på loginsiden, så sender vi et link, der gælder i 30 minutter. En adgangskode er på 12 til 128 tegn. Når du vælger en ny, bliver du logget ud overalt.',
  'help.signin.methods.q': 'Kan jeg logge ind med Google, GitHub eller Microsoft?',
  'help.signin.methods.a':
    'Ja, når de tilbydes på loginsiden. Du kan knytte dem til eller fjerne dem under Login og sikkerhed på kontosiden. Kontoen bruger den e-mailadresse, som udbyderen har bekræftet.',
  'help.signin.twofactor.q': 'Findes der tofaktorgodkendelse?',
  'help.signin.twofactor.a':
    'Ja. Under Login og sikkerhed kan du opsætte en godkendelsesapp. Den beder så om en kode, før du foretager de følsomme ændringer på den side, og du får gendannelseskoder, som kun vises én gang, så gem dem.',
  'help.signin.devices.q': 'Hvordan ser jeg, hvor jeg er logget ind?',
  'help.signin.devices.a':
    'Under Login og sikkerhed ser du de enheder, du er logget ind på, og sikkerhedshændelserne de seneste 30 dage. Du kan logge én enhed ud eller logge ud overalt. Ser du noget, du ikke selv har gjort, så log ud overalt og skift adgangskode.',

  'help.data.title': 'Dine data og sletning af kontoen',
  'help.data.export.q': 'Kan jeg få en kopi af mine data?',
  'help.data.export.a':
    'Ja. På fanen Dine data giver Download mine data dig én fil med din konto, dine hold, sanglister, dit forbrug og din loginhistorik.',
  'help.data.delete.q': 'Hvordan sletter jeg min konto?',
  'help.data.delete.a':
    'På fanen Dine data trykker du på Slet min konto og skriver din e-mailadresse for at bekræfte. Så slettes din konto, dine hold og sanglister, din plan og dit forbrug, dine forbindelser og din loginhistorik. Kørende job stoppes først. Det kan ikke fortrydes. Hold gemt i MOWL og playlister i din Spotify bliver dér, så slet dem i de tjenester, hvis du ikke vil have dem. Mere i {privacy}.',

  'help.contact.title': 'Kontakt',
  'help.contact.p1': 'Skriv til os på {email}.',
  'help.contact.include': 'Skriv gerne med:',
  'help.contact.li1': 'E-mailadressen på din classbeat-konto.',
  'help.contact.li2': 'Hvad du lavede, og hvad du forventede, der ville ske.',
  'help.contact.li3': 'Tidspunktet og fejlteksten, hvis du så en.',
  'help.contact.never':
    'Send aldrig en adgangskode, en login-token, en gendannelseskode eller en engangskode. Vi har aldrig brug for dem.',
  'help.contact.p2':
    'Vil du se, rette, eksportere eller slette dine data, så læs {privacy}. Det meste kan du selv gøre på kontosiden.',
  'help.contact.none': 'Der er ingen supportadresse til rådighed lige nu. Prøv igen senere.',
}
