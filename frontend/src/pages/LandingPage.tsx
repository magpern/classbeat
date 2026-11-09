import { MiniProfile } from '../components/classes/MiniProfile'
import { EXAMPLE_PROFILE, EXAMPLE_SONGS, STEPS, type ExampleSong } from '../components/landing/exampleClass'
import { useT } from '../i18n/I18n'
import { Button } from '../components/ui/button'
import { ListRow } from '../components/ui/list-row'
import { Link } from '../router/Router'

/** The page for visitors who are not signed in (ADR 0013 section 5). */
export function LandingPage() {
  const t = useT()
  const stats = (song: ExampleSong) => t('landing.example.songStats', { bpm: song.bpm, length: song.length })
  return (
    <div className="flex flex-col gap-12">
      <section className="landing flex flex-col gap-5" aria-labelledby="landing-title">
        <p className="badge w-fit">{t('landing.badge')}</p>
        <h1
          id="landing-title"
          className="m-0 font-display text-4xl font-extrabold leading-tight tracking-tight text-balance"
        >
          {t('landing.title')}
        </h1>
        <p className="m-0 max-w-prose text-lg text-muted">{t('landing.lead')}</p>
        <p className="m-0">
          <Button asChild>
            <Link to="/signup">{t('landing.signUp')}</Link>
          </Button>
        </p>
      </section>

      <section aria-labelledby="example-title" className="flex flex-col gap-4">
        <h2 id="example-title" className="m-0 font-display text-2xl font-bold">
          {t('landing.example.title')}
        </h2>
        <p className="m-0 text-muted">{t('landing.example.note')}</p>
        <div className="flex flex-col gap-4 rounded-lg border border-line bg-surface p-4">
          <MiniProfile profile={EXAMPLE_PROFILE} tall />
          <ol
            className="m-0 flex list-none flex-col divide-y divide-line p-0"
            aria-label={t('landing.example.songs')}
          >
            {EXAMPLE_SONGS.map((song, i) => (
              <ListRow
                key={song.title}
                className="px-0 py-2"
                leading={<span className="w-6 shrink-0 text-muted tabular-nums">{String(i + 1)}</span>}
                title={song.title}
                description={
                  <>
                    {t('landing.example.songMeta', { artist: song.artist, part: t(song.part) })}
                    <span className="block tabular-nums sm:hidden">{stats(song)}</span>
                  </>
                }
                action={
                  <span className="hidden text-right text-[0.8125rem] text-muted tabular-nums sm:block">
                    {stats(song)}
                  </span>
                }
              />
            ))}
          </ol>
        </div>
      </section>

      <section aria-labelledby="steps-title" className="flex flex-col gap-4">
        <h2 id="steps-title" className="m-0 font-display text-2xl font-bold">
          {t('landing.how.title')}
        </h2>
        <ol className="m-0 grid list-none grid-cols-1 gap-4 p-0 md:grid-cols-3">
          {STEPS.map((step, i) => (
            <li key={step.title} className="rounded-lg border border-line bg-surface p-5">
              <span
                aria-hidden="true"
                className="mb-3 inline-flex size-9 items-center justify-center rounded-full bg-brand font-display font-bold text-on-brand"
              >
                {String(i + 1)}
              </span>
              <h3 className="m-0 mb-1 font-display text-lg font-bold">{t(step.title)}</h3>
              <p className="m-0 text-[0.9375rem] text-muted">{t(step.text)}</p>
            </li>
          ))}
        </ol>
      </section>
    </div>
  )
}
