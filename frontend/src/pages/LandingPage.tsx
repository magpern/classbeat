import { MiniProfile } from '../components/classes/MiniProfile'
import { EXAMPLE_PROFILE, EXAMPLE_SONGS, STEPS } from '../components/landing/exampleClass'
import { Button } from '../components/ui/button'
import { Link } from '../router/Router'

/** The page for visitors who are not signed in (ADR 0013 section 5). */
export function LandingPage() {
  return (
    <div className="flex flex-col gap-12">
      <section className="landing flex flex-col gap-5" aria-labelledby="landing-title">
        <p className="badge">Private beta</p>
        <h1
          id="landing-title"
          className="m-0 font-display text-4xl font-extrabold leading-tight tracking-tight text-balance"
        >
          Describe your indoor-cycling class, and classbeat picks the songs and times the efforts to the
          music.
        </h1>
        <p className="m-0 flex flex-wrap items-center gap-3">
          <Button asChild>
            <Link to="/signup">Sign up with an invite code</Link>
          </Button>
          <Link to="/signin" className="inline-flex min-h-11 items-center">
            I already have an account
          </Link>
        </p>
      </section>

      <section aria-labelledby="example-title" className="flex flex-col gap-4">
        <h2 id="example-title" className="m-0 font-display text-2xl font-bold">
          An example class
        </h2>
        <p className="m-0 text-muted">
          A made-up 30 minute ride. The songs and artists below are placeholders.
        </p>
        <div className="flex flex-col gap-4 rounded-lg border border-line bg-surface p-4">
          <MiniProfile profile={EXAMPLE_PROFILE} tall />
          <ol className="m-0 flex list-none flex-col p-0" aria-label="Songs of the example class">
            {EXAMPLE_SONGS.map((song, i) => (
              <li
                key={song.title}
                className="grid grid-cols-[1.5rem_1fr_auto] items-center gap-x-3 border-t border-line py-2 first:border-t-0"
              >
                <span className="text-muted tabular-nums">{String(i + 1)}</span>
                <span className="min-w-0">
                  <span className="block truncate font-medium">{song.title}</span>
                  <span className="block truncate text-[0.8125rem] text-muted">
                    {song.artist} · {song.part}
                  </span>
                </span>
                <span className="text-right text-[0.8125rem] text-muted tabular-nums">
                  {String(song.bpm)} BPM · {song.length}
                </span>
              </li>
            ))}
          </ol>
        </div>
      </section>

      <section aria-labelledby="steps-title" className="flex flex-col gap-4">
        <h2 id="steps-title" className="m-0 font-display text-2xl font-bold">
          How it works
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
              <h3 className="m-0 mb-1 font-display text-lg font-bold">{step.title}</h3>
              <p className="m-0 text-[0.9375rem] text-muted">{step.text}</p>
            </li>
          ))}
        </ol>
      </section>
    </div>
  )
}
