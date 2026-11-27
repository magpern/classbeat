import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { ApiError } from '../../api/client'
import { createInterpretJob, type InterpretInput } from '../../api/interpret'
import { useT } from '../../i18n/I18n'
import { forgetExample } from '../../lib/exampleHandoff'
import { exampleKey, type ExampleSlug } from '../../publicSite/examples'
import { newIdempotencyKey } from '../../lib/submittedInput'
import { addPhrase, MAX_REQUEST, recallRequest, rememberRequest } from '../../lib/startRequest'
import { Link, useQuery, useRouter } from '../../router/Router'
import { Notice, useAction } from '../forms'
import { createError } from '../newclass/createError'
import { TextArea } from '../newclass/inputs'
import type { ReadySource } from '../newclass/SourceGate'
import { usePlanAllowance } from '../newclass/usePlanAllowance'
import { PlanNudge } from '../plans/PlanNudge'
import { UpgradeLink } from '../plans/UpgradeLink'
import { Button } from '../ui/button'
import { Chip, ChipButton } from '../ui/chip'
import { BriefFrame, BRIEF_AREA } from './BriefFrame'
import { ClassesLeft } from './ClassesLeft'
import { PlanLength } from './PlanLength'
import { FieldCard } from './FieldCard'
import { STARTERS } from './ideas'
import { IdeasRow } from './IdeasRow'
import { MusicSourcePicker } from './MusicSourcePicker'
import { musicRows } from './musicSources'
import { SpotifyPrompt } from './SpotifyPrompt'
import { spotifyConnectionId, spotifyGate, type SpotifyGate } from './spotifyGate'

type Sport = 'cycling' | 'running'

/**
 * `/classes/new`: describe the class in your own words (ADR 0029 section 5, plan section D). The
 * button starts an `interpret_request` job and goes to the class, which hands the reading on to the
 * design. Nothing here is required but the words; the music, the sport and the ideas only help
 * the reading. The advanced form (every setting) is one link away.
 */
export function StartScreen({ source, example = null }: { source: ReadySource; example?: ExampleSlug | null }) {
  const t = useT()
  const { navigate } = useRouter()
  const from = useQuery().get('from')
  const kept = useMemo(() => (from === null ? null : recallRequest(from)), [from])
  const action = useAction()
  const { allowed, plan, classesLeft, settled } = usePlanAllowance()
  // A request kept for `from` (the revise hand-off) wins over an example: then the example is not used, so it
  // is neither removed nor given focus, and stays kept for a day like any slug the start screen has not used.
  const usedExample = kept === null ? example : null
  const [request, setRequest] = useState(kept?.request ?? (example === null ? '' : t(exampleKey(example, 'prompt'))))
  const [musicKey, setMusicKey] = useState<string | null>(kept?.music ?? null)
  const [sport, setSport] = useState<Sport>(source.runningEnabled && kept?.sport === 'running' ? 'running' : 'cycling')
  const [problem, setProblem] = useState<string | null>(null)
  // The server refused for want of Spotify (`needs_spotify`, or `song_picking_unavailable`: nothing to
  // connect): the same prompt, with the link only when there is something to connect, instead of an error.
  const [refusedForSpotify, setRefusedForSpotify] = useState<SpotifyGate['need'] | null>(null)
  const running = sport === 'running'
  const rows = musicRows(t, {
    playlists: source.playlists,
    mixes: source.mixes,
    songs: source.songs,
    swedebeat: source.swedebeat,
    running,
  })
  // A choice that the sport no longer offers falls back to "classbeat chooses".
  const chosen = rows.find((r) => r.key === musicKey) ?? null
  // One key per distinct request: a repeated click or a retry after a lost answer returns the same job.
  // "Create a class like this" (PP6): the example's own words fill the prompt, which takes focus. This only
  // fills a field: nothing is sent until the instructor presses the button.
  const promptRef = useRef<HTMLTextAreaElement>(null)
  useEffect(() => {
    if (usedExample === null) return
    forgetExample()
    promptRef.current?.focus()
  }, [usedExample])
  const attempt = useRef<{ fingerprint: string; key: string } | null>(null)
  const spotify = spotifyConnectionId(source.songs)
  // A class with music needs Spotify (roadmap Amendment 25): say so before the request is sent.
  const gate = spotifyGate(source.songs, source.swedebeat, chosen?.override ?? null, running)

  const submit = (event: FormEvent) => {
    event.preventDefault()
    const words = request.trim()
    if (words === '') {
      setProblem(t('start.error.empty'))
      return
    }
    if (gate?.blocking) return
    setProblem(null)
    setRefusedForSpotify(null)
    const input: InterpretInput = {
      request: words,
      mowl_connection_id: source.connection.id,
      ...(chosen ? { music_override: chosen.override } : {}),
      ...(running ? { sport_override: 'running' as const } : {}),
      ...(spotify === undefined ? {} : { spotify_connection_id: spotify }),
    }
    const fingerprint = JSON.stringify(input)
    if (attempt.current?.fingerprint !== fingerprint) attempt.current = { fingerprint, key: newIdempotencyKey() }
    const key = attempt.current.key
    void action.run(
      async () => {
        const job = await createInterpretJob(input, key)
        rememberRequest(job.classId, { request: words, music: chosen?.key ?? null, sport })
        navigate(`/classes/${job.classId}`)
      },
      (error) => {
        if (error instanceof ApiError && error.reason === 'needs_spotify') setRefusedForSpotify('connect')
        if (error instanceof ApiError && error.reason === 'song_picking_unavailable') setRefusedForSpotify('unavailable')
        return createError(t, error)
      },
    )
  }

  const shown = problem ?? (refusedForSpotify !== null ? null : action.error)
  const services = [
    t('start.chip.mowl'),
    ...(source.songs.kind === 'ready' || source.songs.kind === 'needs_consent' ? [t('start.chip.spotify')] : []),
    ...(source.swedebeat.kind === 'ready' ? [t('start.chip.swedebeat')] : []),
  ]
  return (
    <form className="flex flex-col gap-5" onSubmit={submit} noValidate aria-label={t('start.formLabel')}>
      {shown ? <Notice kind="error">{shown}</Notice> : null}
      {problem === null && action.upgrade ? <UpgradeLink /> : null}
      <PlanNudge trigger="low" plan={plan} />
      <BriefFrame>
        <TextArea
          ref={promptRef}
          label={t('start.prompt.label')}
          hint={t('start.prompt.hint')}
          placeholder={t('start.prompt.placeholder')}
          rows={5}
          maxLength={MAX_REQUEST}
          value={request}
          className={BRIEF_AREA}
          onChange={(e) => {
            setRequest(e.target.value)
          }}
        />
        <div className="flex flex-wrap gap-2" role="group" aria-label={t('start.starters')}>
          {STARTERS.map((key) => (
            <ChipButton
              key={key}
              pressed={false}
              onClick={() => {
                setRequest(addPhrase(request, t(key)))
              }}
            >
              {t(key)}
            </ChipButton>
          ))}
        </div>
      </BriefFrame>
      <div className="grid gap-3.5 sm:grid-cols-2">
        <FieldCard title={t('start.length.title')}>
          <PlanLength allowed={allowed} settled={settled} />
          {source.runningEnabled ? (
            <div className="flex flex-wrap gap-2" role="group" aria-label={t('newclass.sport')}>
              <ChipButton
                pressed={!running}
                onClick={() => {
                  setSport('cycling')
                }}
              >
                {t('newclass.sport.cycling')}
              </ChipButton>
              <ChipButton
                pressed={running}
                onClick={() => {
                  setSport('running')
                }}
              >
                {t('newclass.sport.running')}
              </ChipButton>
            </div>
          ) : null}
        </FieldCard>
        <FieldCard title={t('start.music.from')}>
          <ul className="m-0 flex list-none flex-wrap gap-2 p-0" aria-label={t('start.services')}>
            {services.map((name) => (
              <li key={name}>
                <Chip tone="ok" dot>
                  {name}
                </Chip>
              </li>
            ))}
          </ul>
          <MusicSourcePicker rows={rows} value={chosen?.key ?? null} onChange={setMusicKey} />
          {gate !== null ? (
            <SpotifyPrompt need={gate.need} soundless={!running} />
          ) : refusedForSpotify !== null ? (
            <SpotifyPrompt need={refusedForSpotify} soundless={!running} />
          ) : null}
        </FieldCard>
      </div>
      <div className="flex flex-wrap items-center gap-4">
        <Button type="submit" disabled={action.busy || gate?.blocking === true} className="min-h-14 px-8 text-[1.0625rem] font-bold">
          {t('start.design')}
        </Button>
        <ClassesLeft count={classesLeft} />
      </div>
      <p className="m-0 text-[0.875rem] text-muted" data-testid="destination">
        {t('start.destination')}
      </p>
      <IdeasRow
        gamesOn={allowed?.gates.games === true}
        running={running}
        onAdd={(phrase) => {
          setRequest(addPhrase(request, phrase))
        }}
      />
      <p className="m-0">
        <Link to="/classes/new/advanced">{t('start.advanced')}</Link>
      </p>
    </form>
  )
}
