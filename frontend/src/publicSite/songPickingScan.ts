// Finds the text of a page that says classbeat picks songs, and the note that must go with it
// (PP4, #893). A song-picking statement needs a Spotify connection with the user's own Client ID and
// access through the plan, so no public page may make one without saying so. Pure.

// Passive and past forms count too: "songs are chosen", "songs picked", "selected songs".
const PICKS = /(?:pick|choos|chos|select)\w*\b[^.!?]{0,30}\bsongs?\b|\bsongs?\b[^.!?]{0,30}\b(?:pick|choos|chos|select)\w*/i

// Swedish and Danish (PP7): the verb stems välj/väl-/plock- and vælg/udvælg/valgt, next to låt- and sang-.
// Letters are matched with Unicode lookarounds because \b does not treat å, ä, ö or æ, ø as letters.
const SV_PICKS =
  /(?<!\p{L})(?:välj|väl[jt]|valde|valda|plock)\p{L}*[^.!?]{0,30}(?<!\p{L})låt(?:ar|arna|en|ens)?(?!\p{L})|(?<!\p{L})låt(?:ar|arna|en)?(?!\p{L})[^.!?]{0,30}(?<!\p{L})(?:välj|väl[jt]|valde|valda|plock)\p{L}*/iu
const DA_PICKS =
  /(?<!\p{L})(?:vælg|udvælg|valg|plukk)\p{L}*[^.!?]{0,30}(?<!\p{L})sang(?:e|ene)?(?!\p{L})|(?<!\p{L})sang(?:e|ene)?(?!\p{L})[^.!?]{0,30}(?<!\p{L})(?:vælg|udvælg|valg|plukk)\p{L}*/iu

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
