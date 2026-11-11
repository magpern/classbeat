import { useT } from '../i18n/I18n'
import { ClassList, ClassRow } from '../components/classes/ClassRow'
import { EmptyPicture } from '../components/imagery/EmptyPicture'
import { SetupChecklist } from '../components/home/SetupChecklist'
import { Notice } from '../components/forms'
import { Button } from '../components/ui/button'
import { useClassList } from '../hooks/useClassList'
import { PRESETS, presetLink } from '../lib/presets'
import { Link } from '../router/Router'

const RECENT = 6

/** Home for a signed-in user (ADR 0028 section 5): one primary action, quick starts, recent classes. */
export function HomePage() {
  const t = useT()
  const { listing, retry } = useClassList()
  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="m-0 font-display text-3xl font-extrabold">{t('home.welcome')}</h1>
        <Button asChild>
          <Link to="/classes/new">{t('home.design.button')}</Link>
        </Button>
      </div>
      <ul className="m-0 flex list-none flex-wrap gap-2 p-0" aria-label={t('home.quick')}>
        {PRESETS.map((preset) => (
          <li key={preset.label}>
            <Link
              to={presetLink(preset)}
              className="chip-lift inline-flex min-h-11 items-center rounded-full border border-line bg-raised px-4 font-medium text-text no-underline hover:bg-line"
            >
              {t(preset.label)}
            </Link>
          </li>
        ))}
      </ul>
      <SetupChecklist />
      <section aria-labelledby="recent-title" className="flex flex-col gap-3">
        <div className="flex flex-wrap items-baseline justify-between gap-x-4">
          <h2 id="recent-title" className="m-0 font-display text-xl font-bold">
            {t('home.recent')}
          </h2>
          {listing.status === 'ready' && listing.classes.length > 0 ? (
            <Link to="/classes">{t('home.seeAll')}</Link>
          ) : null}
        </div>
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
          <EmptyPicture slot="emptyHome">
            <p className="m-0 text-muted">{t('home.empty')}</p>
          </EmptyPicture>
        ) : null}
        {listing.status === 'ready' && listing.classes.length > 0 ? (
          <ClassList>
            {listing.classes.slice(0, RECENT).map((c) => (
              <ClassRow key={c.id} cls={c} />
            ))}
          </ClassList>
        ) : null}
      </section>
    </div>
  )
}
