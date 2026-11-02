import { useCallback, useEffect, useId, useRef, useState } from 'react'
import { ApiError } from '../../api/client'
import type { ClassSummary } from '../../api/classes'
import { failureText } from '../../api/jobMessages'
import { getJob, isTerminal } from '../../api/jobs'
import {
  checkHandoff,
  designOnMix,
  getHandoff,
  needsReconnect,
  notNow,
  overrideHandoff,
  startHandoff,
  swedebeatError,
  type Handoff,
  type SwedebeatMatch,
} from '../../api/swedebeatMatch'
import { getSwedebeatMixes, type SwedebeatMix } from '../../api/swedebeatMixes'
import { minutes } from '../../lib/songs'
import { actionKey, dropActionKey } from '../../lib/submittedInput'
import { useRouter } from '../../router/Router'
import { Notice, useAction } from '../forms'
import { Button } from '../ui/button'
import { Card, CardTitle } from '../ui/card'
import { Chip } from '../ui/chip'
import { ReconnectPrompt } from './ReconnectPrompt'

/** Swedebeat joins songs with about this much overlap (ADR 0016 section 7). */
const CHANGE_SEC = 10

export const timing = { pollMs: 2000 }

function verdictText(handoff: Handoff): { tone: 'ok' | 'warn'; text: string } | null {
  switch (handoff.verdict) {
    case 'ok':
      return { tone: 'ok', text: 'That is your mix. Its length matches your selection.' }
    case 'too_short': {
      const where =
        handoff.missingPositions.length > 0
          ? ` Songs ${handoff.missingPositions.join(', ')} may be missing.`
          : ''
      return { tone: 'warn', text: `That mix is shorter than expected.${where}` }
    }
    case 'too_long':
      return {
        tone: 'warn',
        text: 'That mix is longer than expected. A song may have been added or repeated.',
      }
    case 'not_found':
      return {
        tone: 'warn',
        text: 'Swedebeat has not shown a new mix yet. Finish it in the app, let it sync, then check again.',
      }
    case null:
      return null
  }
}

/**
 * "Next, in the Swedebeat app" (ADR 0016 section 7): classbeat never creates the mix. The page
 * says what to do in the app and has two buttons, "I made the mix" (then "Check again") and "Not
 * now". A verdict never blocks: the user may pick which mix they made, or say it is the right one.
 */
export function NextStep({
  summary,
  match,
  onReload,
}: {
  summary: ClassSummary
  match: SwedebeatMatch
  onReload: () => void
}) {
  const classId = summary.id
  const [handoff, setHandoff] = useState<Handoff | null>(null)
  const [loaded, setLoaded] = useState(false)
  const [expired, setExpired] = useState(false)
  const [dismissed, setDismissed] = useState(false)
  const [reconnect, setReconnect] = useState(false)
  const [mixes, setMixes] = useState<SwedebeatMix[] | null>(null)
  const [mixesFailed, setMixesFailed] = useState(false)
  const [picked, setPicked] = useState<string | null>(null)
  const [waiting, setWaiting] = useState(false)
  const [jobProblem, setJobProblem] = useState<string | null>(null)
  const action = useAction()
  const locked = useRef(false)
  const alive = useRef(true)
  const { navigate } = useRouter()
  const titleId = useId()

  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
    }
  }, [])

  useEffect(() => {
    const abort = new AbortController()
    getHandoff(classId, abort.signal).then(
      (found) => {
        if (abort.signal.aborted) return
        setHandoff(found)
        setLoaded(true)
      },
      (error: unknown) => {
        if (abort.signal.aborted) return
        if (error instanceof ApiError && error.status === 410) setExpired(true)
        setLoaded(true)
      },
    )
    return () => {
      abort.abort()
    }
  }, [classId])

  const checked = handoff !== null && handoff.checkedAt !== null
  const needsPicker = handoff !== null && handoff.state !== 'verified' && handoff.verdict !== null && handoff.verdict !== 'ok'
  useEffect(() => {
    if (!needsPicker || mixes !== null || mixesFailed) return
    const abort = new AbortController()
    getSwedebeatMixes(abort.signal).then(
      (found) => {
        if (!abort.signal.aborted) setMixes(found ?? [])
      },
      () => {
        if (!abort.signal.aborted) setMixesFailed(true)
      },
    )
    return () => {
      abort.abort()
    }
  }, [needsPicker, mixes, mixesFailed])

  const pause = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

  // The verify job is short; read the step again once it ends.
  const waitFor = useCallback(
    async (jobId: string) => {
      setWaiting(true)
      setJobProblem(null)
      try {
        for (;;) {
          await pause(timing.pollMs)
          if (!alive.current) return
          const job = await getJob(jobId)
          if (!isTerminal(job.state)) continue
          if (job.state === 'failed') {
            if (job.errorCategory === 'needs_reconnect') setReconnect(true)
            setJobProblem(failureText(job.errorCategory, job.kind).title)
          } else if (job.state !== 'completed') {
            setJobProblem('The check was stopped. Check again.')
          }
          break
        }
        const fresh = await getHandoff(classId)
        if (alive.current && fresh !== null) setHandoff(fresh)
      } finally {
        if (alive.current) setWaiting(false)
      }
    },
    [classId],
  )

  const describe = (error: unknown) => {
    if (needsReconnect(error)) setReconnect(true)
    if (error instanceof ApiError && error.status === 410) {
      setExpired(true)
      setHandoff(null)
    }
    return swedebeatError(error)
  }

  const press = () => {
    if (locked.current) return
    locked.current = true
    setReconnect(false)
    setExpired(false)
    const scope = `swedebeat-check.${classId}.${handoff?.expiresAt ?? 'new'}.${handoff?.checkedAt ?? 'first'}.${picked ?? 'any'}`
    void action
      .run(async () => {
        let live = handoff
        if (live === null) live = await startHandoff(classId)
        setHandoff(live)
        const done = await checkHandoff(classId, picked, actionKey(scope))
        dropActionKey(scope)
        if (done.handoff !== null) setHandoff(done.handoff)
        if (done.jobId !== null) await waitFor(done.jobId)
      }, describe)
      .finally(() => {
        locked.current = false
      })
  }

  const chosen = picked ?? handoff?.mixId ?? (handoff?.candidates.length === 1 ? handoff.candidates[0]! : null)

  const confirm = () => {
    if (chosen === null) return
    void action.run(async () => {
      setHandoff(await overrideHandoff(classId, chosen))
    }, describe)
  }

  const design = () => {
    if (locked.current) return
    locked.current = true
    const scope = `swedebeat-design.${classId}.${handoff?.expiresAt ?? 'x'}`
    void action
      .run(async () => {
        const done = await designOnMix(classId, actionKey(scope))
        dropActionKey(scope)
        if (done.jobId !== null) navigate(`/jobs/${done.jobId}`)
        else onReload()
      }, describe)
      .finally(() => {
        locked.current = false
      })
  }

  const later = () => {
    void action.run(async () => {
      await notNow(classId)
      setDismissed(true)
    }, describe)
  }

  if (dismissed) {
    return (
      <Card role="region" aria-labelledby={titleId}>
        <CardTitle id={titleId}>Next, in the Swedebeat app</CardTitle>
        <p className="m-0 text-muted">
          Not now. Your selection stays approved; come back to this class when you are ready.
        </p>
        <p className="m-0">
          <Button
            variant="secondary"
            onClick={() => {
              setDismissed(false)
            }}
          >
            Show the steps again
          </Button>
        </p>
      </Card>
    )
  }

  const verdict = handoff === null ? null : verdictText(handoff)
  const expectedSec = Math.max(match.swedebeatSec - CHANGE_SEC * Math.max(match.songs.length - 1, 0), 0)
  const busy = action.busy || waiting
  const verified = handoff?.state === 'verified'
  const playlist = summary.playlist?.name ?? null

  return (
    <Card role="region" aria-labelledby={titleId} className="flex flex-col gap-3" data-testid="next-step">
      <CardTitle id={titleId}>Next, in the Swedebeat app</CardTitle>
      <p className="m-0 text-muted" data-testid="next-facts">
        {playlist !== null ? (
          <>
            Playlist: <strong className="text-text">{playlist}</strong>.{' '}
          </>
        ) : null}
        {match.songs.length} songs, about {minutes(expectedSec)} long as a mix.
      </p>
      <ol className="m-0 flex list-decimal flex-col gap-1 pl-6 text-text">
        <li>Open the Swedebeat app.</li>
        <li>{playlist !== null ? `Match the playlist “${playlist}”.` : 'Match your playlist.'}</li>
        <li>Create the mix.</li>
        <li>Let it sync, then come back here.</li>
      </ol>
      {expired ? (
        <Notice>This step has expired. Press the button to start it again.</Notice>
      ) : null}
      {loaded && verdict !== null ? (
        <p className="notice" data-kind={verdict.tone === 'ok' ? 'success' : 'info'} role="status" data-testid="verdict">
          {verdict.text}
        </p>
      ) : null}
      {waiting ? (
        <p className="m-0 text-muted" role="status">
          Checking your mixes in MOWL
        </p>
      ) : null}
      {jobProblem !== null ? <Notice kind="error">{jobProblem}</Notice> : null}
      {reconnect ? <ReconnectPrompt /> : null}
      {action.error ? <Notice kind="error">{action.error}</Notice> : null}
      {needsPicker ? (
        <MixPicker mixes={mixes} failed={mixesFailed} candidates={handoff.candidates} value={picked} onChange={setPicked} />
      ) : null}
      {verified ? (
        <div className="flex flex-wrap items-center gap-3">
          <Chip tone="ok">Mix verified</Chip>
          <Button disabled={busy || summary.openJob !== null} onClick={design}>
            Design the class on this mix
          </Button>
        </div>
      ) : (
        <div className="flex flex-wrap gap-3">
          <Button disabled={busy || !loaded} onClick={press}>
            {checked ? 'Check again' : 'I made the mix'}
          </Button>
          <Button variant="secondary" disabled={busy} onClick={later}>
            Not now
          </Button>
          {handoff !== null && (handoff.verdict === 'too_short' || handoff.verdict === 'too_long') ? (
            <Button variant="secondary" disabled={busy || chosen === null} onClick={confirm}>
              This is the right mix
            </Button>
          ) : null}
        </div>
      )}
    </Card>
  )
}

function MixPicker({
  mixes,
  failed,
  candidates,
  value,
  onChange,
}: {
  mixes: SwedebeatMix[] | null
  failed: boolean
  candidates: string[]
  value: string | null
  onChange: (id: string) => void
}) {
  const name = useId()
  if (failed) return <Notice kind="error">Your Swedebeat mixes could not be loaded from MOWL. Check again later.</Notice>
  if (mixes === null) return <p className="m-0 text-muted">Loading your mixes</p>
  if (mixes.length === 0) return null
  const ordered = [...mixes].sort((a, b) => Number(candidates.includes(b.mixId)) - Number(candidates.includes(a.mixId)))
  return (
    <fieldset className="m-0 flex flex-col gap-2 rounded-md border border-line p-3">
      <legend className="px-1 text-[0.9375rem] font-medium text-text">Which one did you make?</legend>
      {ordered.map((mix) => {
        const id = `${name}-${mix.mixId}`
        return (
          <label key={mix.mixId} htmlFor={id} className="flex min-h-11 cursor-pointer items-center gap-3 text-text">
            <input
              id={id}
              type="radio"
              name={name}
              checked={value === mix.mixId}
              onChange={() => {
                onChange(mix.mixId)
              }}
            />
            <span className="break-words">
              {mix.name}
              {mix.durationSeconds !== null ? ` (${minutes(mix.durationSeconds)})` : ''}
            </span>
            {candidates.includes(mix.mixId) ? <Chip tone="ok">New</Chip> : null}
          </label>
        )
      })}
    </fieldset>
  )
}
