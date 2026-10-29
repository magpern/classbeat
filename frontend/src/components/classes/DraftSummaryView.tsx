import type { DraftInterval, DraftSummary } from '../../api/jobs'

// A read-only view of what GET /api/jobs/{id}/revision returned. Every string is rendered as
// text by React; nothing is computed here beyond adding up the lengths that are shown.

function clock(seconds: number): string {
  const minutes = Math.floor(seconds / 60)
  const rest = seconds % 60
  return `${String(minutes)}:${String(rest).padStart(2, '0')}`
}

function cadence(rpm: DraftInterval['rpm']): string {
  return rpm === 'beat' ? 'on the beat' : `${String(rpm[0])} to ${String(rpm[1])} rpm`
}

const KINDS: Record<string, string> = {
  warmup: 'Warm-up',
  main: 'Main',
  recovery: 'Recovery',
  cooldown: 'Cool-down',
}

function kindLabel(kind: string): string {
  return Object.hasOwn(KINDS, kind) ? (KINDS[kind] ?? kind) : kind
}

export function DraftSummaryView({ draft }: { draft: DraftSummary }) {
  const total = draft.segments.reduce(
    (sum, segment) => sum + segment.intervals.reduce((inner, i) => inner + i.sec, 0),
    0,
  )
  return (
    <article className="draft" aria-labelledby="draft-title">
      <h2 id="draft-title" className="draft__title">
        {draft.name}
      </h2>
      <p className="muted">
        Draft {draft.number}, {clock(total)} in total
      </p>
      {draft.description ? <p>{draft.description}</p> : null}
      {draft.unknownBpm.length > 0 ? (
        <div className="notice" data-kind="info" role="status" data-testid="unknown-bpm">
          <p>
            MOWL did not know the tempo of these songs yet, so the cadence on them is a guess:
          </p>
          <ul>
            {draft.unknownBpm.map((track) => (
              <li key={track.index}>{track.title}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {draft.segments.map((segment, index) => (
        <section key={index} className="draft__segment" aria-label={segment.name}>
          <h3>
            {segment.name} <span className="muted">{kindLabel(segment.kind)}</span>
          </h3>
          <table className="draft__table">
            <thead>
              <tr>
                <th scope="col">Length</th>
                <th scope="col">Zone</th>
                <th scope="col">Cadence</th>
                <th scope="col">Position</th>
                <th scope="col">Cue</th>
              </tr>
            </thead>
            <tbody>
              {segment.intervals.map((interval, row) => (
                <tr key={row}>
                  <td>{clock(interval.sec)}</td>
                  <td>{interval.zone}</td>
                  <td>{cadence(interval.rpm)}</td>
                  <td>{interval.standing ? 'Standing' : 'Seated'}</td>
                  <td>{interval.cue}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ))}
    </article>
  )
}
