import { useEffect, useMemo } from 'react'
import { useT } from '../i18n/I18n'
import { SourceGate } from '../components/newclass/SourceGate'
import { ComposerScreen } from '../components/start/ComposerScreen'
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
  useEffect(() => {
    // Kept while the MOWL connection or the sources are missing; the start screen removes it when it uses it.
    if (fromLink !== null) rememberExample(fromLink)
    else if (kept === null) forgetExample()
  }, [fromLink, kept])
  return (
    <RequireAuth>
      <div className="mx-auto flex w-full max-w-3xl flex-col gap-5">
        <h1 className="m-0 font-display text-3xl font-extrabold">{t('newclass.title')}</h1>
        <SourceGate>
          {(source) =>
            source.composerFlow ? (
              <ComposerScreen source={source} example={example} />
            ) : (
              <StartScreen source={source} example={example} />
            )
          }
        </SourceGate>
      </div>
    </RequireAuth>
  )
}
