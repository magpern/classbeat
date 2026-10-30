import type { Revision } from '../../api/classes'
import { originLabel } from '../../api/classMessages'
import { clock, segmentSeconds, totalSeconds, tss } from '../../lib/workout'
import { TimelineChart } from './TimelineChart'

const KINDS: Record<string, string> = {
  warmup: 'Warm-up',
  main: 'Main',
  recovery: 'Recovery',
  cooldown: 'Cool-down',
}

function cadence(rpm: [number, number] | 'beat'): string {
  return rpm === 'beat' ? 'on the beat' : `${String(rpm[0])} to ${String(rpm[1])} rpm`
}

/**
 * One version of the class: numbers, warnings, the timeline chart and the segments. Names,
 * descriptions, cues and song titles are model or user text and are rendered as text only.
 * `musicChanged` is the backend's flag from the answer that made this version, when it has one.
 * The API carries no song list per segment; the songs it names are the ones without a known BPM.
 */
export function RevisionView({
  revision,
  musicChanged,
}: {
  revision: Revision
  musicChanged: boolean
}) {
  const seconds = totalSeconds(revision.segments)
  return (
    <article className="draft" aria-labelledby="revision-title">
      <h2 id="revision-title" className="draft__title">
        {revision.name}
      </h2>
      <p className="muted">
        Version {revision.number}, {originLabel(revision.origin).toLowerCase()}
      </p>
      <dl className="facts">
        <div>
          <dt>Length</dt>
          <dd data-testid="duration">{clock(seconds)}</dd>
        </div>
        <div>
          <dt>TSS</dt>
          <dd data-testid="tss">{tss(revision.segments)}</dd>
        </div>
        <div>
          <dt>Segments</dt>
          <dd>{revision.segments.length}</dd>
        </div>
      </dl>
      {revision.description ? <p>{revision.description}</p> : null}
      {musicChanged ? (
        <div className="notice" data-kind="info" role="status" data-testid="music-changed">
          <p>
            The music changed since the version before: the playlist in MOWL is not the one this
            class was built on, so check that the songs still fit.
          </p>
        </div>
      ) : null}
      {revision.unknownBpm.length > 0 ? (
        <div className="notice" data-kind="info" role="status" data-testid="unknown-bpm">
          <p>MOWL did not know the tempo of these songs yet, so the cadence on them is a guess:</p>
          <ul>
            {revision.unknownBpm.map((track) => (
              <li key={track.index}>{track.title}</li>
            ))}
          </ul>
        </div>
      ) : null}
      <TimelineChart segments={revision.segments} title={revision.name} />
      {revision.segments.map((segment, index) => (
        <section key={index} className="draft__segment" aria-label={segment.name}>
          <h3>
            {segment.name}{' '}
            <span className="muted">
              {Object.hasOwn(KINDS, segment.kind) ? KINDS[segment.kind] : segment.kind},{' '}
              {clock(segmentSeconds(segment))}
            </span>
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
