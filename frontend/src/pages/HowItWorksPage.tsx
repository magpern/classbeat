import { AvailabilityNote } from '../components/publicPages/AvailabilityNote'
import { JoinBetaBand } from '../components/publicPages/JoinBetaBand'
import { useT } from '../i18n/I18n'
import { CONNECTIONS, STEPS } from '../publicSite/howItWorks'

/** How it works (PP4, #893): the four steps and what you connect. A draft until PP8. */
export function HowItWorksPage() {
  const t = useT()
  return (
    <article className="mx-auto flex max-w-3xl flex-col gap-8" aria-labelledby="how-title">
      <header className="flex flex-col gap-3">
        <h1 id="how-title" className="m-0 font-display text-4xl font-extrabold leading-tight tracking-tight">
          {t('how.title')}
        </h1>
        <p className="m-0 text-lg">{t('how.lead')}</p>
      </header>
      <section aria-labelledby="how-steps" className="flex flex-col gap-3">
        <h2 id="how-steps" className="m-0 font-display text-2xl font-bold">
          {t('how.steps.title')}
        </h2>
        <ol className="m-0 flex list-none flex-col gap-4 p-0">
          {STEPS.map((step, i) => (
            <li key={step.id} className="fill-step flex gap-4">
              <span
                aria-hidden="true"
                className="fill-step__badge mt-4 inline-flex size-9 shrink-0 items-center justify-center rounded-full bg-brand font-display font-bold text-on-brand"
              >
                {String(i + 1)}
              </span>
              <div className="fill-step__card glow-card flex min-w-0 flex-1 flex-col gap-1 rounded-lg border border-line bg-surface p-5">
                <h3 className="m-0 font-display text-lg font-bold">{t(step.title)}</h3>
                <p className="m-0">{t(step.text)}</p>
              </div>
            </li>
          ))}
        </ol>
      </section>
      <section aria-labelledby="how-connect" className="flex flex-col gap-3">
        <h2 id="how-connect" className="m-0 font-display text-2xl font-bold">
          {t('how.connect.title')}
        </h2>
        <p className="m-0">{t('how.connect.lead')}</p>
        <ul className="m-0 flex list-none flex-col gap-4 p-0">
          {CONNECTIONS.map((row) => (
            <li key={row.id} className="reveal glow-card flex flex-col gap-1 rounded-lg border border-line bg-surface p-5">
              <h3 className="m-0 font-display text-lg font-bold">{t(row.name)}</h3>
              <p className="m-0">{t(row.text)}</p>
              <AvailabilityNote claim={row} />
            </li>
          ))}
        </ul>
      </section>
      <JoinBetaBand />
    </article>
  )
}
