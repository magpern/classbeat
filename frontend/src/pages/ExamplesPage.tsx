import { ExampleCard } from '../components/examples/ExampleCard'
import { useT } from '../i18n/I18n'
import { EXAMPLES } from '../publicSite/examples'

/** The list of example classes (ADR 0030, PP5): made-up, synthetic, carrying no music. */
export function ExamplesPage() {
  const t = useT()
  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-8">
      <header className="flex flex-col gap-3">
        <h1 className="m-0 font-display text-4xl font-extrabold leading-tight tracking-tight">{t('examples.title')}</h1>
        <p className="m-0 text-lg">{t('examples.lead')}</p>
        <p className="m-0 text-muted">{t('examples.note')}</p>
      </header>
      <ul className="reveal-row m-0 grid list-none gap-4 p-0 sm:grid-cols-2" aria-label={t('examples.list')}>
        {EXAMPLES.map((example) => (
          <ExampleCard key={example.slug} example={example} />
        ))}
      </ul>
    </div>
  )
}
