// The hash of a revision (ADR 0007 section 2): SHA-256 of its canonical JSON, the same bytes as
// the backend's `classbeat.design.revision.canonical` (sorted keys, no spaces, UTF-8, non-ASCII
// kept). "Use this version" sends the hash of the content the user is looking at, so the server
// refuses (409 `hash_mismatch`) when the revision is not the one that was shown.

function sortedJson(value: unknown): string {
  if (value === null || typeof value !== 'object') return JSON.stringify(value)
  if (Array.isArray(value)) return `[${value.map(sortedJson).join(',')}]`
  const record = value as Record<string, unknown>
  const keys = Object.keys(record).sort(compareCodePoints)
  return `{${keys.map((key) => `${JSON.stringify(key)}:${sortedJson(record[key])}`).join(',')}}`
}

// Python sorts keys by code point, JavaScript's default by UTF-16 unit; they differ above U+FFFF.
function compareCodePoints(a: string, b: string): number {
  const left = Array.from(a)
  const right = Array.from(b)
  for (let i = 0; i < Math.min(left.length, right.length); i++) {
    const x = left[i]!.codePointAt(0)!
    const y = right[i]!.codePointAt(0)!
    if (x !== y) return x - y
  }
  return left.length - right.length
}

export function canonicalJson(value: unknown): string {
  return sortedJson(value)
}

/** Lower-case hex SHA-256 of the canonical JSON of `content`. */
export async function contentHash(content: unknown): Promise<string> {
  const subtle = globalThis.crypto?.subtle as SubtleCrypto | undefined
  if (subtle === undefined) throw new Error('This browser cannot compute the version hash.')
  const bytes = new TextEncoder().encode(canonicalJson(content))
  const digest = await subtle.digest('SHA-256', bytes)
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, '0')).join('')
}
