import { Fragment, type ReactNode } from 'react'
import { Trans, useT } from '../../i18n/I18n'
import { Link } from '../../router/Router'
import { legalKey, type LegalSection } from './structure'

const EMAIL = 'support@classbeat.eu'

/** The facts the repository cannot supply, in one marked box (issue #718): never invented. */
function ControllerBlock() {
  const t = useT()
  const rows = ['name', 'address', 'company', 'hosting'] as const
  return (
    <aside
      className="my-4 rounded-lg border border-dashed border-line bg-raised p-4"
      aria-labelledby="legal-controller"
      data-placeholder="controller"
    >
      <h3 id="legal-controller" className="m-0 font-display text-lg font-bold">
        {t('legal.controller.title')}
      </h3>
      <p className="mt-1 mb-3 text-muted">{t('legal.controller.note')}</p>
      <dl className="m-0 grid gap-x-4 gap-y-2 sm:grid-cols-[max-content_1fr]">
        {rows.map((row) => (
          <Fragment key={row}>
            <dt className="font-medium">{t(`legal.controller.${row}`)}</dt>
            <dd className="m-0">{t('legal.controller.value')}</dd>
          </Fragment>
        ))}
      </dl>
    </aside>
  )
}

function Text({ id }: { id: ReturnType<typeof legalKey> }) {
  const t = useT()
  return (
    <Trans
      id={id}
      slots={{
        email: <a href={`mailto:${EMAIL}`}>{EMAIL}</a>,
        terms: <Link to="/terms">{t('legal.inline.terms')}</Link>,
        privacy: <Link to="/privacy">{t('legal.inline.privacy')}</Link>,
      }}
    />
  )
}

/** A title, a lead and sections of paragraphs and lists, all from the catalogue. */
export function LegalDoc({ doc, sections }: { doc: 'privacy' | 'terms'; sections: readonly LegalSection[] }) {
  const t = useT()
  return (
    <article className="mx-auto flex max-w-3xl flex-col gap-8" aria-labelledby="legal-title">
      <header className="flex flex-col gap-3">
        <h1
          id="legal-title"
          className="m-0 font-display text-4xl font-extrabold leading-tight tracking-tight"
        >
          {t(`${doc}.title` as Parameters<typeof t>[0])}
        </h1>
        <p className="m-0 text-lg">{t(`${doc}.lead` as Parameters<typeof t>[0])}</p>
        <p className="m-0 text-muted">{t('legal.updated')}</p>
      </header>
      {sections.map((section) => {
        const blocks: ReactNode[] = []
        let items: string[] = []
        const flush = () => {
          if (items.length === 0) return
          const list = items
          blocks.push(
            <ul key={`ul-${list[0]}`} className="m-0 flex flex-col gap-2 pl-6">
              {list.map((part) => (
                <li key={part}>
                  <Text id={legalKey(doc, section.id, part)} />
                </li>
              ))}
            </ul>,
          )
          items = []
        }
        section.parts.forEach((part, index) => {
          if (part.startsWith('li')) {
            items.push(part)
            return
          }
          flush()
          blocks.push(
            <p key={part} className="m-0">
              <Text id={legalKey(doc, section.id, part)} />
            </p>,
          )
          if (section.controller === true && index === 0) blocks.push(<ControllerBlock key="controller" />)
        })
        flush()
        return (
          <section key={section.id} aria-labelledby={`legal-${section.id}`} className="flex flex-col gap-3">
            <h2 id={`legal-${section.id}`} className="m-0 font-display text-2xl font-bold">
              {t(legalKey(doc, section.id, 'title'))}
            </h2>
            {blocks}
          </section>
        )
      })}
    </article>
  )
}
