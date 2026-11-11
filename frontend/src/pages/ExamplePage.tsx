import { ProfileChart } from '../components/classes/ProfileChart'
import { ExampleTable } from '../components/examples/ExampleTable'
import { useT } from '../i18n/I18n'
import { Link } from '../router/Router'
import { GOAL_FIELDS, exampleKey, exampleSeconds, type Example } from '../publicSite/examples'

/**
 * One example class (ADR 0030, PP5): the brief, who it is for, what it aims at, the request, the
 * chart and a table that says the same in text. No songs go to the chart: the song strip sets
 * `style`, which the CSP forbids in served HTML, and an example carries no music.
 */
export function ExamplePage({ example }: { example: Example }) {
  const t = useT()
  const slug = example.slug
  const running = example.sport === 'running'
  const title = t(exampleKey(slug, 'title'))
  return (
    <article className="mx-auto flex max-w-4xl flex-col gap-8" aria-labelledby="example-title" data-example={slug}>
      <header className="flex flex-col gap-3">
        <p className="m-0">
          <Link to="/examples">{t('examples.back')}</Link>
        </p>
        <h1 id="example-title" className="m-0 font-display text-4xl font-extrabold leading-tight tracking-tight">
          {title}
        </h1>
        <p className="m-0 text-lg">{t(exampleKey(slug, 'summary'))}</p>
      </header>

      <section aria-label={t('examples.facts')}>
        <dl className="m-0 grid gap-x-6 gap-y-3 sm:grid-cols-[max-content_1fr]">
          <dt className="font-medium text-muted">{t('examples.fact.sport')}</dt>
          <dd className="m-0">{t(`examples.sport.${example.sport}`)}</dd>
          <dt className="font-medium text-muted">{t('examples.fact.length')}</dt>
          <dd className="m-0 tabular-nums" data-testid="example-length">
            {t('examples.length', { minutes: exampleSeconds(example) / 60 })}
          </dd>
          <dt className="font-medium text-muted">{t('examples.fact.audience')}</dt>
          <dd className="m-0">{t(exampleKey(slug, 'audience'))}</dd>
          <dt className="font-medium text-muted">{t('examples.fact.goals')}</dt>
          <dd className="m-0">
            <ul className="m-0 flex list-disc flex-col gap-1 pl-5">
              {GOAL_FIELDS.map((field) => (
                <li key={field}>{t(exampleKey(slug, field))}</li>
              ))}
            </ul>
          </dd>
        </dl>
      </section>

      <section aria-labelledby="example-request" className="flex flex-col gap-3">
        <h2 id="example-request" className="m-0 font-display text-2xl font-bold">
          {t('examples.request.title')}
        </h2>
        <p className="m-0 text-muted">{t('examples.request.lead')}</p>
        <blockquote className="reveal m-0 rounded-lg border border-line bg-raised p-4 text-lg" data-testid="example-prompt">
          {t(exampleKey(slug, 'prompt'))}
        </blockquote>
      </section>

      <section aria-labelledby="example-class" className="flex flex-col gap-4">
        <h2 id="example-class" className="m-0 font-display text-2xl font-bold">
          {t('examples.class.title')}
        </h2>
        <p className="m-0 text-muted">{t('examples.class.lead')}</p>
        <div className="draw-in">
          <ProfileChart segments={example.segments} title={title} songs={null} />
        </div>
        <ExampleTable segments={example.segments} title={title} running={running} />
      </section>

      <section aria-labelledby="example-cta" className="reveal glow-card flex flex-col gap-3 rounded-lg border border-line bg-surface p-5">
        <h2 id="example-cta" className="m-0 font-display text-2xl font-bold">
          {t('examples.cta.title')}
        </h2>
        <p className="m-0">{t('examples.cta.text')}</p>
        <p className="m-0">
          <Link className="button" to={`/classes/new?example=${slug}`}>
            {t('examples.cta.button')}
          </Link>
        </p>
      </section>

      <section aria-labelledby="example-notes" className="flex flex-col gap-2">
        <h2 id="example-notes" className="m-0 font-display text-2xl font-bold">
          {t('examples.notes.title')}
        </h2>
        <ul className="m-0 flex list-disc flex-col gap-1 pl-5">
          <li>{t('examples.notes.music')}</li>
          <li>{t('examples.notes.english')}</li>
          {running ? <li>{t('examples.notes.running')}</li> : null}
        </ul>
      </section>
    </article>
  )
}
