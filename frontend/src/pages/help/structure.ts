import type { CatalogueKey } from '../../i18n/en'

/** One topic of the help page: its id and the question ids in order. */
export interface HelpTopic {
  id: string
  questions: readonly string[]
}

// Keys are `help.<topic>.<question>.q` and `.a`, and `help.<topic>.title` (issue #650).
export const HELP_TOPICS: readonly HelpTopic[] = [
  { id: 'connect', questions: ['mowl', 'spotify', 'swedebeat'] },
  { id: 'design', questions: ['how', 'saved', 'change'] },
  { id: 'plan', questions: ['limits', 'code'] },
  { id: 'signin', questions: ['reset', 'methods', 'twofactor', 'devices'] },
  { id: 'data', questions: ['export', 'delete'] },
]

export const CONTACT_PARTS = ['p1', 'include', 'li1', 'li2', 'li3', 'never', 'p2'] as const

export const helpKey = (topic: string, part: string) => `help.${topic}.${part}` as CatalogueKey

/** Every catalogue key the page uses, for the test that checks each language has them all. */
export function helpKeys(): CatalogueKey[] {
  return [
    'help.title',
    'help.lead',
    ...HELP_TOPICS.flatMap((topic) => [
      helpKey(topic.id, 'title'),
      ...topic.questions.flatMap((q) => [helpKey(topic.id, `${q}.q`), helpKey(topic.id, `${q}.a`)]),
    ]),
    'help.contact.title',
    ...CONTACT_PARTS.map((part) => helpKey('contact', part)),
    'help.contact.none',
  ]
}
