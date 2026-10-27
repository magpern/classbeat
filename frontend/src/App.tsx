import { ApiStatus } from './components/ApiStatus'
import { Logo } from './components/Logo'

export function App() {
  return (
    <>
      <header className="app-header">
        <div className="app-header__inner">
          <span className="brand">
            <Logo />
            <span className="brand__name">classbeat</span>
          </span>
          <ApiStatus />
        </div>
      </header>
      <main className="app-main">
        <section className="landing" aria-labelledby="landing-title">
          <p className="badge">Private beta</p>
          <h1 id="landing-title" className="landing__title">
            Describe your indoor-cycling class, and classbeat picks the songs and times the efforts
            to the music.
          </h1>
        </section>
      </main>
    </>
  )
}
