// Tests the pure decision logic of the npm audit gate with fixture reports. Run: node --test scripts/npm-audit-gate.test.mjs
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { ALLOWLIST, MAX_DAYS_AHEAD, decide } from './npm-audit-gate-policy.mjs'

const ID = 'GHSA-fc5q-39xc-vjpf'
const advisory = (id, severity) => ({ source: 1, name: 'x', url: `https://github.com/advisories/${id}`, severity })
const report = (vulnerabilities) => ({ auditReportVersion: 2, vulnerabilities })
const allow = [{ id: ID, expires: '2026-12-17', reason: 'test' }]
const picomatchTree = {
  picomatch: { severity: 'high', via: [advisory(ID, 'high')] },
  fdir: { severity: 'high', via: ['picomatch'] },
  vite: { severity: 'high', via: ['picomatch', 'fdir'] },
  eslint: { severity: 'moderate', via: ['minimatch'] },
  minimatch: { severity: 'moderate', via: [advisory('GHSA-g3hv-75wg-m249', 'moderate')] },
}

test('only the allowlisted advisory: pass', () => {
  const r = decide(report(picomatchTree), allow, '2026-11-17')
  assert.equal(r.ok, true)
  assert.match(r.lines.join('\n'), /tolerated GHSA-fc5q-39xc-vjpf/)
})

test('the last day of the expiry still passes, the next day fails with a clear message', () => {
  assert.equal(decide(report(picomatchTree), allow, '2026-12-17').ok, true)
  const r = decide(report(picomatchTree), allow, '2026-12-18')
  assert.equal(r.ok, false)
  assert.match(r.lines.join('\n'), /allowlist expired 2026-12-17/)
})

test('a package with the allowlisted advisory and another high advisory fails', () => {
  const tree = { ...picomatchTree, vite: { severity: 'high', via: ['picomatch', advisory('GHSA-aaaa-bbbb-cccc', 'high')] } }
  const r = decide(report(tree), allow, '2026-11-17')
  assert.equal(r.ok, false)
  assert.match(r.lines.join('\n'), /vite: GHSA-aaaa-bbbb-cccc/)
})

test('an unrelated high advisory fails', () => {
  const r = decide(report({ lodash: { severity: 'high', via: [advisory('GHSA-aaaa-bbbb-cccc', 'high')] } }), allow, '2026-11-17')
  assert.equal(r.ok, false)
})

test('a critical advisory fails even when allowlisted', () => {
  const tree = { picomatch: { severity: 'critical', via: [advisory(ID, 'critical')] } }
  assert.equal(decide(report(tree), allow, '2026-11-17').ok, false)
})

test('moderate findings alone and an empty report pass', () => {
  const r = decide(report({ eslint: picomatchTree.eslint, minimatch: picomatchTree.minimatch }), allow, '2026-11-17')
  assert.equal(r.ok, true)
  assert.equal(decide(report({}), allow, '2026-11-17').ok, true)
})

test('malformed or unknown shapes fail', () => {
  const t = '2026-11-17'
  assert.equal(decide(null, allow, t).ok, false)
  assert.equal(decide({ error: { code: 'ENOAUDIT' } }, allow, t).ok, false)
  assert.equal(decide({ auditReportVersion: 1, vulnerabilities: {} }, allow, t).ok, false)
  assert.equal(decide(report({ a: { severity: 'high', via: ['missing'] } }), allow, t).ok, false)
  assert.equal(decide(report({ a: { severity: 'high', via: [{ title: 'no url' }] } }), allow, t).ok, false)
  assert.equal(decide(report({ a: { severity: 'weird', via: [] } }), allow, t).ok, false)
  assert.equal(decide(report({ a: { severity: 'high', via: [] } }), allow, t).ok, false)
  assert.equal(decide(report({ a: { severity: 'high', via: 'x' } }), allow, t).ok, false)
})

test('a cyclic via chain terminates and still reaches its advisory', () => {
  const tree = { a: { severity: 'high', via: ['b'] }, b: { severity: 'high', via: ['a', advisory(ID, 'high')] } }
  assert.equal(decide(report(tree), allow, '2026-11-17').ok, true)
})

test('an allowlist entry without a valid expiry or a reason fails the gate', () => {
  for (const bad of [{ id: ID, reason: 'r' }, { id: ID, expires: 'soon', reason: 'r' }, { id: ID, expires: '2026-12-17' }]) {
    assert.equal(decide(report(picomatchTree), [bad], '2026-11-17').ok, false)
  }
})

test('the real ALLOWLIST is well formed', () => {
  for (const e of ALLOWLIST) assert.equal(decide(report({}), [e], '2026-11-17').ok, true)
})

test('a package that is itself critical fails even if its high advisories are all allowlisted', () => {
  const tree = { picomatch: { severity: 'critical', via: [advisory(ID, 'high')] } }
  const r = decide(report(tree), allow, '2026-11-17')
  assert.equal(r.ok, false)
  assert.match(r.lines.join('\n'), /picomatch: the package itself is critical/)
})

test('an expiry must be a real calendar date', () => {
  for (const expires of ['2026-99-99', '2026-02-30', '2026-13-01', '2026-00-10', '2026-12-17x']) {
    const r = decide(report(picomatchTree), [{ id: ID, expires, reason: 'r' }], '2026-11-17')
    assert.equal(r.ok, false, expires)
    assert.match(r.lines.join('\n'), /real expiry date/)
  }
  assert.equal(decide(report({}), allow, '2026-11-31').ok, false)
})

test(`an expiry more than ${MAX_DAYS_AHEAD} days ahead fails, exactly ${MAX_DAYS_AHEAD} passes`, () => {
  const entry = (expires) => [{ id: ID, expires, reason: 'r' }]
  assert.equal(decide(report(picomatchTree), entry('2027-01-16'), '2026-11-17').ok, true) // 60 days
  const r = decide(report(picomatchTree), entry('2027-01-17'), '2026-11-17') // 61 days
  assert.equal(r.ok, false)
  assert.match(r.lines.join('\n'), /more than 60 days/)
  // the clock is injected: the real entry is fine on the day it was written and fails if it were far ahead
  assert.equal(decide(report({}), ALLOWLIST, '2026-11-17').ok, true)
  assert.equal(decide(report({}), ALLOWLIST, '2026-01-01').ok, false)
})

test('a high package whose via lists a missing package next to the allowlisted advisory fails', () => {
  const tree = { vite: { severity: 'high', via: [advisory(ID, 'high'), 'not-in-the-report'] } }
  const r = decide(report(tree), allow, '2026-11-17')
  assert.equal(r.ok, false)
  assert.match(r.lines.join('\n'), /unknown package "not-in-the-report"/)
})

test('a moderate leaf next to the allowlisted leaf passes (the shape of typescript-estree)', () => {
  const tree = {
    ...picomatchTree,
    'typescript-estree': { severity: 'high', via: ['minimatch', 'picomatch'] },
    'both-direct': { severity: 'high', via: [advisory('GHSA-g3hv-75wg-m249', 'moderate'), advisory(ID, 'high')] },
  }
  const r = decide(report(tree), allow, '2026-11-17')
  assert.equal(r.ok, true)
  assert.doesNotMatch(r.lines.join('\n'), /GHSA-g3hv/)
})
