import { useT } from '../i18n/I18n'
import { AdvancedForm } from '../components/newclass/AdvancedForm'
import { SourceGate } from '../components/newclass/SourceGate'
import { RequireAuth } from '../components/RequireAuth'
import { Link } from '../router/Router'

/** `/classes/new/advanced`: the form with every setting (the start screen's Advanced link, and the Home presets). */
export function AdvancedClassPage() {
  const t = useT()
  return (
    <RequireAuth>
      <div className="mx-auto flex w-full max-w-5xl flex-col gap-5">
        <h1 className="m-0 font-display text-3xl font-extrabold">{t('newclass.title')}</h1>
        <p className="m-0 text-[0.9375rem]">
          <Link to="/classes/new">{t('start.backToStart')}</Link>
        </p>
        <SourceGate>
          {(source) => (
            <AdvancedForm
              connection={source.connection}
              playlists={source.playlists}
              songs={source.songs}
              swedebeat={source.swedebeat}
              mixes={source.mixes}
              runningEnabled={source.runningEnabled}
              soundlessEnabled={source.soundlessEnabled}
            />
          )}
        </SourceGate>
      </div>
    </RequireAuth>
  )
}
