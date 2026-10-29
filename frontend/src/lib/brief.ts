import type { Brief } from '../api/jobs'

// The brief is stored on the class as jsonb and may not exceed 4096 bytes there (backend
// `classbeat.design.revision.MAX_BRIEF_BYTES`, checked when the job is created). The limit is in
// bytes, not characters: an accented or non-Latin letter takes two to four. The size is measured
// the way the backend does: control and format characters removed, tabs and newlines as spaces,
// trimmed, then printed the way jsonb prints it (`", "` and `": "` separators), as UTF-8.

export const MAX_BRIEF_BYTES = 4096

export function cleanText(value: string): string {
  return value
    .replace(/[\t\n\r]/g, ' ')
    .replace(/[\p{Cc}\p{Cf}]/gu, '')
    .trim()
}

function jsonb(value: unknown): string {
  if (value === null || typeof value !== 'object') return JSON.stringify(value)
  if (Array.isArray(value)) return `[${value.map(jsonb).join(', ')}]`
  const record = value as Record<string, unknown>
  return `{${Object.keys(record)
    .map((key) => `${JSON.stringify(key)}: ${jsonb(record[key])}`)
    .join(', ')}}`
}

export function briefBytes(brief: Brief): number {
  const cleaned = { ...brief, style: cleanText(brief.style), text: cleanText(brief.text) }
  return new TextEncoder().encode(jsonb(cleaned)).length
}

export const BRIEF_TOO_LONG =
  'The style and description are too long for one class (the limit is 4096 bytes in all, and ' +
  'accented or non-Latin letters count for more than one). Shorten them and try again.'
