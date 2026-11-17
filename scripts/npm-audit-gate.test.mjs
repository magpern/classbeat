// Tests the pure decision logic of the npm audit gate with fixture reports. Run: node --test scripts/npm-audit-gate.test.mjs
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { ALLOWLIST, decide } from './npm-audit-gate-policy.mjs'

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
