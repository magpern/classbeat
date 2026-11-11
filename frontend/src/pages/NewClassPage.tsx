import { useEffect, useMemo } from 'react'
import { useT } from '../i18n/I18n'
import { SourceGate } from '../components/newclass/SourceGate'
import { StartScreen } from '../components/start/StartScreen'
import { RequireAuth } from '../components/RequireAuth'
import { recallExample, rememberExample, validExample } from '../lib/exampleHandoff'
import { useQuery } from '../router/Router'

/** `/classes/new`: the free-text start screen. The form with every setting is `/classes/new/advanced`. */
export function NewClassPage() {
  const t = useT()
  const fromLink = validExample(useQuery().get('example'))
  // The link wins; else a slug kept for a day by sign-up or the MOWL connection (PP6). Unknown values are ignored.
  const example = useMemo(() => fromLink ?? recallExample(), [fromLink])
  // Kept while the MOWL connection or the sources are missing; the start screen removes it when it uses it.
  useEffect(() => {
    if (fromLink !== null) rememberExample(fromLink)
  }, [fromLink])
  return (
    <RequireAuth>
      <div className="mx-auto flex w-full max-w-3xl flex-col gap-5">
        <h1 className="m-0 font-display text-3xl font-extrabold">{t('newclass.title')}</h1>
        <SourceGate>{(source) => <StartScreen source={source} example={example} />}</SourceGate>
      </div>
    </RequireAuth>
  )
}
