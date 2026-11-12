import { type ReactNode } from 'react'
import { useT } from '../../i18n/I18n'
import { draftKey, DRAFT_STRUCTURE, type DraftDoc } from './structure'

/** A draft legal page: a banner that says it is a draft, a title, a lead and sections, all from the catalogue (PB-4, #975). */
export function LegalDraftDoc({ doc }: { doc: DraftDoc }) {
  const t = useT()
  return (
    <article className="mx-auto flex max-w-3xl flex-col gap-8" aria-labelledby="draft-title" data-testid="legal-draft">
      <aside className="rounded-lg border border-dashed border-line bg-raised p-4" aria-labelledby="draft-banner">
        <h2 id="draft-banner" className="m-0 font-display text-lg font-bold">
          {t('draft.banner.title')}
        </h2>
        <p className="mt-1 mb-0 text-muted">{t('draft.banner.body')}</p>
      </aside>
      <header className="flex flex-col gap-3">
        <h1 id="draft-title" className="m-0 font-display text-4xl font-extrabold leading-tight tracking-tight">
          {t(`draft.${doc}.title`)}
        </h1>
        <p className="m-0 text-lg">{t(`draft.${doc}.lead`)}</p>
      </header>
      {DRAFT_STRUCTURE[doc].map((section) => {
        const blocks: ReactNode[] = []
        let items: string[] = []
        const flush = () => {
          if (items.length === 0) return
          const list = items
          blocks.push(
            <ul key={`ul-${list[0]}`} className="m-0 flex flex-col gap-2 pl-6">
              {list.map((part) => (
                <li key={part}>{t(draftKey(doc, section.id, part))}</li>
              ))}
            </ul>,
          )
          items = []
        }
        for (const part of section.parts) {
          if (part.startsWith('li')) {
            items.push(part)
            continue
          }
          flush()
          blocks.push(
            <p key={part} className="m-0">
              {t(draftKey(doc, section.id, part))}
            </p>,
          )
        }
        flush()
        return (
          <section key={section.id} aria-labelledby={`draft-${section.id}`} className="flex flex-col gap-3">
            <h2 id={`draft-${section.id}`} className="m-0 font-display text-2xl font-bold">
              {t(draftKey(doc, section.id, 'title'))}
            </h2>
            {blocks}
          </section>
        )
      })}
    </article>
  )
}
