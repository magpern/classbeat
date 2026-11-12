import type { CatalogueKey } from '../../i18n/en'

/** One section of a draft legal page: its id and the parts in order (`p…` paragraph, `li…` list item). */
export interface DraftSection {
  id: string
  parts: readonly string[]
}

export type DraftDoc = 'terms' | 'privacy' | 'refunds' | 'subprocessors'

export const DRAFT_STRUCTURE: Readonly<Record<DraftDoc, readonly DraftSection[]>> = {
  terms: [
    { id: 'service', parts: ['p1', 'p2', 'p3'] },
    { id: 'account', parts: ['li1', 'li2', 'li3'] },
    { id: 'plans', parts: ['p1', 'p2', 'p3', 'p4'] },
    { id: 'content', parts: ['p1', 'p2'] },
    { id: 'use', parts: ['li1', 'li2', 'li3'] },
    { id: 'liability', parts: ['p1'] },
    { id: 'ending', parts: ['p1', 'p2'] },
    { id: 'law', parts: ['p1', 'p2'] },
  ],
  privacy: [
    { id: 'who', parts: ['p1'] },
    { id: 'data', parts: ['p1', 'li1', 'li2', 'li3', 'li4', 'li5', 'li6', 'p2'] },
    { id: 'basis', parts: ['p1'] },
    { id: 'model', parts: ['p1'] },
    { id: 'processors', parts: ['p1'] },
    { id: 'retention', parts: ['p1', 'p2'] },
    { id: 'rights', parts: ['p1'] },
    { id: 'transfers', parts: ['p1'] },
  ],
  refunds: [
    { id: 'rule', parts: ['p1', 'p2', 'p3'] },
    { id: 'law', parts: ['p1'] },
    { id: 'provider', parts: ['p1'] },
    { id: 'contact', parts: ['p1'] },
  ],
  subprocessors: [
    { id: 'model', parts: ['p1', 'p2'] },
    { id: 'music', parts: ['p1', 'p2'] },
    { id: 'signin', parts: ['p1'] },
    { id: 'security', parts: ['p1'] },
    { id: 'mail', parts: ['p1'] },
    { id: 'hosting', parts: ['p1'] },
    { id: 'payments', parts: ['p1'] },
    { id: 'update', parts: ['p1'] },
  ],
}

/** The language-neutral path of each draft page. Terms and privacy carry `-draft` because the beta pages own `/terms` and `/privacy`. */
export const DRAFT_ROUTES: Readonly<Record<DraftDoc, string>> = {
  terms: '/terms-draft',
  privacy: '/privacy-draft',
  refunds: '/refunds',
  subprocessors: '/sub-processors',
}

export const draftKey = (doc: DraftDoc, id: string, part: string) => `draft.${doc}.${id}.${part}` as CatalogueKey

/** Every catalogue key a draft page uses, for the test that checks the catalogue has them all. */
export function draftKeys(doc: DraftDoc): CatalogueKey[] {
  return [
    `meta.draft.${doc}.title`,
    `meta.draft.${doc}.description`,
    `draft.${doc}.title`,
    `draft.${doc}.lead`,
    ...DRAFT_STRUCTURE[doc].flatMap((s) => [draftKey(doc, s.id, 'title'), ...s.parts.map((p) => draftKey(doc, s.id, p))]),
  ] as CatalogueKey[]
}
