import { describe, expect, it } from 'vitest'
import { briefBytes, cleanText, MAX_BRIEF_BYTES } from './brief'
import { canonicalJson, contentHash } from './canonical'

// The expected values were made with the backend's own functions:
// hashlib.sha256(json.dumps(v, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode())
describe('the version hash', () => {
  const content = {
    name: 'Tüesday "climb" é 日本',
    description: '',
    segments: [
      {
        name: 'Warm up',
        kind: 'warmup',
        intervals: [
          { sec: 300, zone: 2, rpm: [80, 90], standing: false, cue: 'Easy\nnow <b>' },
          { sec: 90, zone: 5, rpm: 'beat', standing: true, game: 'racetrack' },
        ],
      },
    ],
  }

  it('is the canonical JSON of the backend: sorted keys, no spaces, non-ASCII kept', () => {
    expect(canonicalJson(content)).toBe(
      '{"description":"","name":"Tüesday \\"climb\\" é 日本","segments":[{"intervals":[{"cue":"Easy\\nnow <b>","rpm":[80,90],"sec":300,"standing":false,"zone":2},{"game":"racetrack","rpm":"beat","sec":90,"standing":true,"zone":5}],"kind":"warmup","name":"Warm up"}]}',
    )
  })

  it('is the SHA-256 of those bytes, as lower-case hex', async () => {
    expect(await contentHash(content)).toBe(
      '72a3e3a4b5aba90b2f1c3f48f07aa5b88849e56168d55cf11e19f9f8e7633ee0',
    )
  })

  it('does not depend on the key order of the answer', async () => {
    const reverse = (value: unknown): unknown =>
      Array.isArray(value)
        ? value.map(reverse)
        : typeof value === 'object' && value !== null
          ? Object.fromEntries(
              Object.entries(value)
                .reverse()
                .map(([k, v]) => [k, reverse(v)]),
            )
          : value
    const reversed = reverse(content)
    expect(await contentHash(reversed)).toBe(await contentHash(content))
  })
})

describe('the brief size', () => {
  const brief = {
    length_min: 45,
    intensity: 'moderate' as const,
    climbs: 0,
    sprints: 0,
    races: 0,
    ftp_test: null,
    style: '',
    text: '',
  }

  it('is measured as jsonb prints it, in UTF-8 bytes', () => {
    // {"climbs": 0, "ftp_test": null, "intensity": "moderate", "length_min": 45, "races": 0,
    //  "sprints": 0, "style": "", "text": ""} is 125 bytes from the backend's jsonb_size.
    expect(briefBytes(brief)).toBe(125)
  })

  it('counts bytes, not characters', () => {
    const letters = 'å'.repeat(1500) // 1500 characters, 3000 bytes
    expect(briefBytes({ ...brief, text: letters })).toBe(125 + 3000)
    expect(briefBytes({ ...brief, text: letters + letters })).toBeGreaterThan(MAX_BRIEF_BYTES)
    // 2000 characters (the field's limit) of plain letters still fit.
    expect(briefBytes({ ...brief, text: 'a'.repeat(2000) })).toBeLessThan(MAX_BRIEF_BYTES)
  })

  it('cleans the text the way the backend does before it counts', () => {
    expect(cleanText('  a\tb​c\u0007\n ')).toBe('a bc')
  })
})
