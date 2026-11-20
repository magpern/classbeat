import { useT } from '../i18n/I18n'
import { ClassCard } from '../components/classes/ClassCard'
import { EmptyPicture } from '../components/imagery/EmptyPicture'
import { SetupChecklist } from '../components/home/SetupChecklist'
import { Notice } from '../components/forms'
import { HomeHero } from '../components/home/HomeHero'
import { Button } from '../components/ui/button'
import { Card, CardContent } from '../components/ui/card'
import { PageHeading } from '../components/ui/page-heading'
import { useClassList } from '../hooks/useClassList'
import { DeleteClassFromList } from '../components/classes/DeleteClassFromList'
import { Link } from '../router/Router'

const RECENT = 6

/** Home for a signed-in user (ADR 0028 section 5): one primary action, recent classes. */
export function HomePage() {
  const t = useT()
  const { listing, retry } = useClassList()
  return (
    <div className="flex flex-col gap-7">
      <PageHeading eyebrow={t('home.eyebrow')} title={t('home.welcome')} intro={t('home.intro')} titleClassName="leading-none" />
      <HomeHero />
      <SetupChecklist />
      <section aria-labelledby="recent-title" className="flex flex-col gap-4">
        <PageHeading
          as="h2"
          size="section"
          titleId="recent-title"
          title={t('home.recent')}
          actions={
            listing.status === 'ready' && listing.classes.length > 0 ? (
              <Link to="/classes">{t('home.seeAll')}</Link>
            ) : undefined
          }
        />
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
          <Card>
            <EmptyPicture slot="emptyHome">
              <CardContent>{t('home.empty')}</CardContent>
            </EmptyPicture>
          </Card>
        ) : null}
        {listing.status === 'ready' && listing.classes.length > 0 ? (
          <ul className="m-0 grid list-none grid-cols-1 gap-4 p-0 sm:grid-cols-[repeat(auto-fill,minmax(20rem,1fr))]">
            {listing.classes.slice(0, RECENT).map((c) => (
              <ClassCard
                key={c.id}
                cls={c}
                // The confirmation says what stays in MOWL and in Swedebeat: classbeat deletes neither.
                actions={<DeleteClassFromList summary={c} onDeleted={retry} />}
              />
            ))}
          </ul>
        ) : null}
      </section>
    </div>
  )
}
