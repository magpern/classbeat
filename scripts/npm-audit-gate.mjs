// Usage: node scripts/npm-audit-gate.mjs <dir>...   (CI "npm audit" step, ADR 0037)
// Runs `npm audit --audit-level=high --json` in each directory and applies the allowlist of
// npm-audit-gate-policy.mjs. Exit 0 only when every directory passes.
import { spawnSync } from 'node:child_process'
import { ALLOWLIST, decide } from './npm-audit-gate-policy.mjs'

const dirs = process.argv.slice(2)
if (dirs.length === 0) {
  console.error('usage: npm-audit-gate.mjs <dir>...')
  process.exit(2)
}
const today = new Date().toISOString().slice(0, 10)
let failed = false
for (const dir of dirs) {
  // npm exits non-zero when it finds something; the JSON on stdout is what counts.
  const run = spawnSync('npm', ['audit', '--audit-level=high', '--json'], {
    cwd: dir,
    encoding: 'utf8',
    maxBuffer: 64 * 1024 * 1024,
  })
  let audit = null
  try {
    audit = JSON.parse(run.stdout)
  } catch {
    // decide() reports a non-report as a failure
  }
  const { ok, lines } = decide(audit, ALLOWLIST, today)
  console.log(`npm audit gate ${dir}: ${ok ? 'ok' : 'FAILED'}`)
  for (const l of lines) console.log(`  ${l}`)
  if (!ok) failed = true
}
process.exit(failed ? 1 : 0)
