import { en, type CatalogueKey } from '../i18n/en'
import type { TFunction } from '../i18n/t'
import { isObject, optionalText, type JsonObject } from './decode'

// Server sentences as codes (ADR 0023 section 6): the backend sends a code and its numbers next to
// its English text. The page words the code from the catalogue (`sentence.<code>`) and shows the
// server's text for a code it does not know. A code is only ever looked up as an own key of the
// English catalogue, so a made-up code cannot reach anything else.

export interface SentenceRef {
  code: string
  /** Whole numbers only; `count` picks the plural form. */
  params: Record<string, number>
}

/** Reads `<codeKey>` and `<paramsKey>` of an answer; null when there is no code. */
export function decodeSentence(source: JsonObject, codeKey: string, paramsKey: string): SentenceRef | null {
  const code = optionalText(source, codeKey)
  if (code === null) return null
  const params: Record<string, number> = {}
  const raw = source[paramsKey]
  if (isObject(raw)) {
    for (const [name, value] of Object.entries(raw)) {
      if (typeof value === 'number' && Number.isSafeInteger(value)) {
        params[name] = value
      }
    }
  }
  return { code, params }
}

function catalogueKey(code: string): CatalogueKey | null {
  const key = `sentence.${code}`
  return Object.hasOwn(en, key) ? (key as CatalogueKey) : null
}

/** The sentence in the page's language, else the server's own text (null if it sent none). */
export function sentenceText(t: TFunction, ref: SentenceRef | null, fallback: string | null): string | null {
  const key = ref === null ? null : catalogueKey(ref.code)
  return ref !== null && key !== null ? t(key, ref.params) : fallback
}
