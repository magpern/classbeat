import { ClaimList } from '../components/publicPages/ClaimList'
import { JoinBetaBand } from '../components/publicPages/JoinBetaBand'
import { useT } from '../i18n/I18n'
import { FEATURE_GROUPS } from '../publicSite/claims'

/** Features (PP4, #893): what classbeat does and what each feature needs. A draft until PP8. */
export function FeaturesPage() {
  const t = useT()
  return (
    <article className="mx-auto flex max-w-3xl flex-col gap-8" aria-labelledby="features-title">
      <header className="flex flex-col gap-3">
        <h1 id="features-title" className="m-0 font-display text-4xl font-extrabold leading-tight tracking-tight">
          {t('feat.title')}
        </h1>
        <p className="m-0 text-lg">{t('feat.lead')}</p>
      </header>
      {FEATURE_GROUPS.map((group) => (
        <section key={group.id} aria-labelledby={`feat-${group.id}`} className="flex flex-col gap-3">
          <h2 id={`feat-${group.id}`} className="m-0 font-display text-2xl font-bold">
            {t(group.title)}
          </h2>
          <ClaimList claims={group.claims} />
        </section>
      ))}
      <JoinBetaBand />
    </article>
  )
}
