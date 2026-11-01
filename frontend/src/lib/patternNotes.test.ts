import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { RULE_COPY, valueText } from './patternNotes'

const DESIGN = join(import.meta.dirname, '..', '..', '..', 'backend', 'src', 'classbeat', 'design')

function backendRuleIds(): string[] {
  const source = readFileSync(join(DESIGN, 'rules', 'enums.py'), 'utf8')
  const block = /RuleId = Literal\[([^\]]*)\]/.exec(source)?.[1] ?? ''
  return [...block.matchAll(/"([a-z0-9_]+)"/g)].map((m) => m[1] ?? '')
}

function rationales(): Record<string, string> {
  const source = readFileSync(join(DESIGN, 'rules', 'rationales.py'), 'utf8')
  const found: Record<string, string> = {}
  for (const m of source.matchAll(/^\s+"([a-z0-9_]+)":\s*"([^"]+)",?\s*$/gm)) {
    found[m[1] ?? ''] = m[2] ?? ''
  }
  return found
}

describe('pattern note copy', () => {
  it('has copy for every backend rule id, and nothing else', () => {
    const ids = backendRuleIds()
    expect(ids.length).toBeGreaterThan(10)
    expect(Object.keys(RULE_COPY).sort()).toEqual([...ids].sort())
  })

  it('mirrors the backend rationale of each rule in every shipped profile', () => {
    const table = rationales()
    expect(Object.keys(table).length).toBeGreaterThan(10)
    const dir = join(DESIGN, 'profiles', 'cycling')
    const seen = new Set<string>()
    for (const name of readdirSync(dir).filter((f) => f.endsWith('.json'))) {
      const profile = JSON.parse(readFileSync(join(dir, name), 'utf8')) as {
        rules: { rule: string; rationale_id: string }[]
      }
      for (const rule of profile.rules) {
        seen.add(rule.rule)
        expect(RULE_COPY[rule.rule], `${name} ${rule.rule}`).toBe(table[rule.rationale_id])
      }
    }
    expect(seen.size).toBeGreaterThan(5)
  })

  it('says what is usual, never a verdict', () => {
    for (const sentence of Object.values(RULE_COPY)) {
      expect(sentence).toMatch(/^Most classes of this kind /)
      expect(sentence).not.toMatch(/wrong|bad|correct/i)
    }
  })
})

describe('valueText', () => {
  it('shows the value against the usual range', () => {
    expect(valueText({ metric: 'first_zone4plus_s', value: 240, bounds: [370, null] })).toBe(
      'This one: 4:00. Usual: 6:10 or more',
    )
    expect(valueText({ metric: 'cooldown_s', value: 300, bounds: [null, 188] })).toBe(
      'This one: 5:00. Usual: up to 3:08',
    )
    expect(valueText({ metric: 'zone4plus_time_share', value: 0.62, bounds: [0.2, 0.5] })).toBe(
      'This one: 62%. Usual: 20% to 50%',
    )
    expect(valueText({ metric: 'mean_zone', value: 4.26, bounds: [null, 3.9] })).toBe(
      'This one: 4.3. Usual: up to 3.9',
    )
  })
})
