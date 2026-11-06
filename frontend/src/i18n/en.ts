// The source catalogue (ADR 0023 section 3): English, keys by feature. Other languages
// are typed as a partial `Catalogue`; a missing key shows this text. A value is a string with
// named `{name}` placeholders, or plural forms chosen by the `count` parameter.
// No markup in a string: links and emphasis are slots of `Trans`.

export const en = {
  'format.soonUnknown': 'a few minutes from now',
  'usage.underOneCent': 'under {amount}',
} as const satisfies Record<string, Message>

export type Plural = { one?: string; other: string; zero?: string; two?: string; few?: string; many?: string }
export type Message = string | Plural

export type CatalogueKey = keyof typeof en
/** What a non-English catalogue may hold: some of the keys, never others. */
export type Catalogue = Partial<Record<CatalogueKey, Message>>
