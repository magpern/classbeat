import { useEffect, useMemo, useState } from 'react'
import { useT } from '../i18n/I18n'
import { SourceGate } from '../components/newclass/SourceGate'
import { ComposerScreen } from '../components/start/ComposerScreen'
import { StartAside } from '../components/start/StartAside'
import { StartScreen } from '../components/start/StartScreen'
import { RequireAuth } from '../components/RequireAuth'
import { forgetExample, recallExample, rememberExample, validExample } from '../lib/exampleHandoff'
import { useQuery } from '../router/Router'

/**
 * `/classes/new`: the composer with the `composer_flow` flag on (ADR 0038 section 10.1), else the free-text start
 * screen. The form with every setting is `/classes/new/advanced`.
 */
export function NewClassPage() {
  const t = useT()
  const fromLink = validExample(useQuery().get('example'))
  // The link wins; else a slug kept for a day by sign-up or the MOWL connection (PP6). Unknown values are ignored.
  // `recallExample` only reads, so render has no side effect; an expired entry is removed in the effect below.
  const kept = useMemo(() => (fromLink === null ? recallExample() : null), [fromLink])
  const example = fromLink ?? kept
  // The aside (sketch and next steps) shows only once the sources are ready, not while loading or failed.
  const [ready, setReady] = useState(false)
  useEffect(() => {
    // Kept while the MOWL connection or the sources are missing; the start screen removes it when it uses it.
    if (fromLink !== null) rememberExample(fromLink)
    else if (kept === null) forgetExample()
  }, [fromLink, kept])
  return (
    <RequireAuth>
      <div className="mx-auto grid w-full max-w-6xl gap-8 lg:grid-cols-[minmax(0,1fr)_21.5rem] lg:items-start">
        <div className="flex min-w-0 flex-col gap-6">
          <header className="flex flex-col gap-2.5">
            <h1 className="m-0 font-display text-4xl font-extrabold leading-none sm:text-5xl">{t('newclass.title')}</h1>
            <p className="m-0 max-w-[32.5rem] text-[1.0625rem] leading-normal text-muted">{t('start.hero.intro')}</p>
          </header>
          <SourceGate onReady={setReady}>
            {(source) =>
              source.composerFlow ? (
                <ComposerScreen source={source} example={example} />
              ) : (
                <StartScreen source={source} example={example} />
              )
            }
          </SourceGate>
        </div>
        {ready ? <StartAside /> : null}
      </div>
    </RequireAuth>
  )
}
