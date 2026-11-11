// Finds the text of a page that says classbeat picks songs, and the note that must go with it
// (PP4, #893). A song-picking statement needs a Spotify connection with the user's own Client ID and
// access through the plan, so no public page may make one without saying so. Pure.

// Passive and past forms count too: "songs are chosen", "songs picked", "selected songs".
const PICKS = /(?:pick|choos|chos|select)\w*\b[^.!?]{0,30}\bsongs?\b|\bsongs?\b[^.!?]{0,30}\b(?:pick|choos|chos|select)\w*/i

// Swedish and Danish (PP7): the verb stems välj/väl-/valt/vald/utval/plock- and vælg/udvælg/udvalg/valg/pluk-, next to
// låt-/sång- and sang-/numre. The Danish stem is `pluk` (the imperative "Pluk sangene" has one k; "plukker" has two).
// Letters are matched with Unicode lookarounds because \b does not treat å, ä, ö or æ, ø as letters.
const SV_VERB = String.raw`(?:välj|väl[jt]|valde|valda|valt|vald|utval|plock)\p{L}*`
const SV_NOUN = String.raw`(?:låt(?:ar|arna|en|ens)?|sång(?:er|erna|en|ens)?)`
const DA_VERB = String.raw`(?:vælg|udvælg|udvalg|valg|pluk)\p{L}*`
const DA_NOUN = String.raw`(?:sang(?:e|en|ene)?|numre(?:ne)?)`
// A verb stem within 30 characters of a noun, in either order, inside one sentence.
const near = (verb: string, noun: string) =>
  new RegExp(
    String.raw`(?<!\p{L})${verb}[^.!?]{0,30}(?<!\p{L})${noun}(?!\p{L})|(?<!\p{L})${noun}(?!\p{L})[^.!?]{0,30}(?<!\p{L})${verb}`,
    'iu',
  )
const SV_PICKS = near(SV_VERB, SV_NOUN)
const DA_PICKS = near(DA_VERB, DA_NOUN)

export function mentionsSongPicking(text: string): boolean {
  return PICKS.test(text) || SV_PICKS.test(text) || DA_PICKS.test(text)
}

/** One sentence that names Spotify, the Client ID and the plan. */
export function hasSongPickingNote(text: string): boolean {
  return text
    .split(/(?<=[.!?])\s+/)
    .some((sentence) => /spotify/i.test(sentence) && /client id/i.test(sentence) && /\bplan\b/i.test(sentence))
}

/** The visible text of a prerendered body: tags dropped, the few entities React writes decoded. */
export function textOf(html: string): string {
  return html
    .replace(/<[^>]*>/g, ' ')
    .replace(/&#x27;|&#39;|&apos;/g, "'")
    .replace(/&quot;/g, '"')
    .replace(/&amp;/g, '&')
    .replace(/\s+/g, ' ')
    .trim()
}
