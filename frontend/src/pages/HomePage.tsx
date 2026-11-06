import { useSession } from '../auth/SessionContext'
import { useT } from '../i18n/I18n'
import { ClassCard } from '../components/classes/ClassCard'
import { SetupChecklist } from '../components/home/SetupChecklist'
import { Notice } from '../components/forms'
import { Button } from '../components/ui/button'
import { Card, CardContent, CardTitle } from '../components/ui/card'
import { useClassList } from '../hooks/useClassList'
import { PRESETS, presetLink } from '../lib/presets'
import { Link } from '../router/Router'

const RECENT = 6

/** Home for a signed-in user (ADR 0013 section 5). */
export function HomePage() {
  const t = useT()
  const { session } = useSession()
  const { listing, retry } = useClassList()
  const name = (session?.user?.email ?? '').split('@')[0] ?? ''
  return (
    <div className="flex flex-col gap-6">
      <h1 className="m-0 font-display text-3xl font-extrabold">{name === '' ? t('home.welcome') : t('home.welcomeName', { name })}</h1>
      <Card aria-labelledby="design-title" role="region">
        <CardTitle id="design-title">{t('home.design.title')}</CardTitle>
        <CardContent>
          <p className="mt-0">{t('home.design.text')}</p>
          <ul className="m-0 mb-4 flex list-none flex-wrap gap-2 p-0" aria-label={t('home.design.presets')}>
            {PRESETS.map((preset) => (
              <li key={preset.label}>
                <Link
                  to={presetLink(preset)}
                  className="inline-flex min-h-11 items-center rounded-full border border-line bg-raised px-4 font-medium text-text no-underline hover:bg-line"
                >
                  {preset.label}
                </Link>
              </li>
            ))}
          </ul>
          <Button asChild>
            <Link to="/classes/new">{t('home.design.button')}</Link>
          </Button>
        </CardContent>
      </Card>
      <SetupChecklist />
      <section aria-labelledby="recent-title" className="flex flex-col gap-3">
        <h2 id="recent-title" className="m-0 font-display text-xl font-bold">
          {t('home.recent')}
        </h2>
        {listing.status === 'loading' ? <p className="m-0 text-muted">{t('home.loading')}</p> : null}
        {listing.status === 'failed' ? (
          <>
            <Notice kind="error">{t('home.loadFailed')}</Notice>
            <p className="m-0">
              <Button variant="secondary" onClick={retry}>
                {t('common.tryAgain')}
              </Button>
            </p>
          </>
        ) : null}
        {listing.status === 'ready' && listing.classes.length === 0 ? (
          <p className="m-0 text-muted">{t('home.empty')}</p>
        ) : null}
        {listing.status === 'ready' && listing.classes.length > 0 ? (
          <>
            <ul className="m-0 grid list-none grid-cols-1 gap-4 p-0 sm:grid-cols-2 xl:grid-cols-3">
              {listing.classes.slice(0, RECENT).map((c) => (
                <li key={c.id}>
                  <ClassCard cls={c} />
                </li>
              ))}
            </ul>
            <p className="m-0">
              <Link to="/classes">{t('home.seeAll')}</Link>
            </p>
          </>
        ) : null}
      </section>
    </div>
  )
}
