// Finds the text of a page that says classbeat picks songs, and the note that must go with it
// (PP4, #893). A song-picking statement needs a Spotify connection with the user's own Client ID and
// access through the plan, so no public page may make one without saying so. Pure.

const PICKS = /(?:pick|choos)\w*\b[^.!?]{0,30}\bsongs?\b|\bsongs?\b[^.!?]{0,30}\b(?:pick|choos)\w*/i

export function mentionsSongPicking(text: string): boolean {
  return PICKS.test(text)
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
