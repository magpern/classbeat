import { describe, expect, it } from 'vitest'
import { failureText, retryLabel, reviseFailureText } from './jobMessages'
import da from '../i18n/da'
import { en } from '../i18n/en'
import sv from '../i18n/sv'

// ADR 0040 amendment item 4 (#1352): a job failed `analysis_unavailable` says the music analysis is
// not available right now, that it is retried by itself and that the user may try again. No
// technical detail: no host, no service name, no category code.

const KEYS = [
  'job.failure.analysis_unavailable.title',
  'job.failure.analysis_unavailable.detail',
  'job.retry.analysis_unavailable',
  'job.revise.analysis_unavailable',
] as const

// A service or product name, a code, a URL, a bare host (`example.com`) or an IP (`10.0.0.5`).
const TECHNICAL = /spotdown|classbeat|analysis_unavailable|https?:|\w\.\w|\d+\.\d+\.\d+/i

describe('analysis_unavailable copy', () => {
  it('shows its own title and detail, not the fallback and not analysis_failed', () => {
    const found = failureText('analysis_unavailable')
    expect(found.title).toBe('Music analysis is not available right now')
    // Shown only once the job has ended, so it says the retries were made, not that they will be.
    expect(found.detail).toMatch(/tried several times/)
    expect(found.detail).not.toMatch(/\b(try|trying) again by ourselves|will try again/i)
    expect(found.detail).toMatch(/try again in a few minutes/i)
    expect(found).not.toEqual(failureText('analysis_failed'))
    expect(found).not.toEqual(failureText('some_unknown_category'))
    expect(JSON.stringify(found)).not.toMatch(TECHNICAL)
  })

  it('is retried by itself while the job waits, and says nothing changed on a revise', () => {
    expect(retryLabel('analysis_unavailable')).toBe('Music analysis is not available right now. Trying again shortly.')
    expect(retryLabel('analysis_unavailable')).not.toBe(retryLabel(null))
    expect(reviseFailureText('analysis_unavailable')).toMatch(/Nothing changed/)
    expect(reviseFailureText('analysis_unavailable')).not.toBe(reviseFailureText(null))
  })

  it.each([
    ['sv', sv],
    ['da', da],
  ] as const)('has every string in %s, different from English and without technical words', (_lang, catalogue) => {
    for (const key of KEYS) {
      expect(catalogue[key], key).toBeTypeOf('string')
      expect(catalogue[key], key).not.toBe(en[key])
      expect(catalogue[key] as string, key).not.toMatch(TECHNICAL)
    }
  })

  it('the technical-words pattern catches a name, a URL, a bare host and an IP', () => {
    for (const bad of ['spotdown is down', 'see https://x', 'spotdown.internal', 'at example.com', 'host 10.0.0.5']) {
      expect(bad).toMatch(TECHNICAL)
    }
    expect('We tried several times. Try again in a few minutes.').not.toMatch(TECHNICAL)
  })
})
