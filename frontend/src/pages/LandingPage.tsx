import { MiniProfile } from '../components/classes/MiniProfile'
import { HowItWorks } from '../components/landing/HowItWorks'
import { LandingHero } from '../components/landing/LandingHero'
import { EXAMPLE_PROFILE, EXAMPLE_SONGS, type ExampleSong } from '../components/landing/exampleClass'
import { useT } from '../i18n/I18n'
import { ListRow } from '../components/ui/list-row'

// The sample songs' own words (title, artist, BPM, length) are English on purpose. The e2e check for
// English text on sv and da cuts exactly these out of the list and still reads every translated string.
const FIXTURE_TEXT = EXAMPLE_SONGS.flatMap((s) => [s.title, s.artist, String(s.bpm), s.length]).join('|')

/** The page for visitors who are not signed in (ADR 0013 section 5). */
export function LandingPage() {
  const t = useT()
  const stats = (song: ExampleSong) => t('landing.example.songStats', { bpm: song.bpm, length: song.length })
  return (
    <div className="flex flex-col gap-12">
      <LandingHero />

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
            data-testid="landing-example-songs"
            data-fixture-text={FIXTURE_TEXT}
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

      <HowItWorks />
    </div>
  )
}
