import type { CatalogueKey } from '../../i18n/en'

/** One section of a legal page: its id and the parts in order (`p…` paragraph, `li…` list item). */
export interface LegalSection {
  id: string
  parts: readonly string[]
  /** The controller placeholder box goes right after this section's first paragraph. */
  controller?: boolean
  /** Parts shown after `parts` that sv and da do not have yet (ADR 0023 section 8): English until the translation PR moves them. */
  englishOnly?: readonly string[]
}

export const PRIVACY: readonly LegalSection[] = [
  { id: 'who', parts: ['p1'], controller: true },
  { id: 'data', parts: ['p1', 'li1', 'li2', 'li3', 'li4', 'li5', 'li6', 'li7'] },
  { id: 'basis', parts: ['p1', 'li1', 'li2', 'li3'] },
  { id: 'model', parts: ['p1', 'p2'] },
  { id: 'processors', parts: ['p1', 'li1', 'li2', 'li3', 'li4', 'li5', 'li6', 'li7', 'p2'] },
  { id: 'cookies', parts: ['p1', 'li1', 'li2', 'li3', 'li4', 'li5', 'p2', 'p3', 'p4'], englishOnly: ['p5'] },
  { id: 'retention', parts: ['li1', 'li2', 'li3', 'li4', 'li5', 'li6', 'li7', 'p1', 'p2'] },
  { id: 'rights', parts: ['p1', 'p2', 'p3'] },
  { id: 'changes', parts: ['p1'] },
]

export const TERMS: readonly LegalSection[] = [
  { id: 'service', parts: ['p1', 'p2'] },
  { id: 'account', parts: ['li1', 'li2', 'li3'] },
  { id: 'content', parts: ['p1', 'p2'] },
  { id: 'use', parts: ['li1', 'li2', 'li3'] },
  { id: 'liability', parts: ['p1'] },
  { id: 'ending', parts: ['p1'] },
  { id: 'law', parts: ['p1', 'p2'] },
]

export const legalKey = (doc: 'privacy' | 'terms', id: string, part: string) =>
  `${doc}.${id}.${part}` as CatalogueKey

/** Every catalogue key a page uses, for the test that checks each language has them all. */
export function legalKeys(doc: 'privacy' | 'terms'): CatalogueKey[] {
  const sections = doc === 'privacy' ? PRIVACY : TERMS
  return [
    `${doc}.title`,
    `${doc}.lead`,
    ...sections.flatMap((s) => [
      legalKey(doc, s.id, 'title'),
      ...s.parts.map((part) => legalKey(doc, s.id, part)),
    ]),
  ] as CatalogueKey[]
}
