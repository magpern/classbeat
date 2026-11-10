import { useT } from '../i18n/I18n'
import { SourceGate } from '../components/newclass/SourceGate'
import { StartScreen } from '../components/start/StartScreen'
import { RequireAuth } from '../components/RequireAuth'

/** `/classes/new`: the free-text start screen. The form with every setting is `/classes/new/advanced`. */
export function NewClassPage() {
  const t = useT()
  return (
    <RequireAuth>
      <div className="mx-auto flex w-full max-w-3xl flex-col gap-5">
        <h1 className="m-0 font-display text-3xl font-extrabold">{t('newclass.title')}</h1>
        <SourceGate>{(source) => <StartScreen source={source} />}</SourceGate>
      </div>
    </RequireAuth>
  )
}
