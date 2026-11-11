import { useT } from '../../i18n/I18n'
import { Link } from '../../router/Router'
import { exampleKey, exampleProfile, exampleSeconds, type Example } from '../../publicSite/examples'
import { MiniProfile } from '../classes/MiniProfile'

/** A library card for an example class: its profile (the same intervals as its page), title, sport and length. */
export function ExampleCard({ example }: { example: Example }) {
  const t = useT()
  const id = `example-${example.slug}`
  return (
    <li className="reveal glow-card flex flex-col gap-3 rounded-lg border border-line bg-surface p-4" data-example={example.slug}>
      <MiniProfile profile={exampleProfile(example)} motion="hover" />
      <h2 id={id} className="m-0 font-display text-xl font-bold">
        <Link to={`/examples/${example.slug}`} className="text-text underline-offset-4 hover:underline">
          {t(exampleKey(example.slug, 'title'))}
        </Link>
      </h2>
      <p className="m-0 text-muted tabular-nums">
        {t('examples.card.meta', {
          sport: t(`examples.sport.${example.sport}`),
          length: t('examples.length', { minutes: exampleSeconds(example) / 60 }),
        })}
      </p>
      <p className="m-0">{t(exampleKey(example.slug, 'audience'))}</p>
    </li>
  )
}
