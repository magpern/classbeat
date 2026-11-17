// Pure decision logic of the npm audit gate (ADR 0037): no I/O, no npm, so the tests feed it
// fixture JSON. It reads the output of `npm audit --json` (auditReportVersion 2).
//
// `npm audit --audit-level=high` fails on any high or critical package. This gate fails on the
// same things except an advisory listed in ALLOWLIST, and only until that entry's expiry date.

// The one place where an advisory is tolerated. Every entry needs a reason and an expiry
// (YYYY-MM-DD, UTC); after it the advisory fails the gate again. Only a `high` advisory can be
// tolerated: a `critical` one never is. Remove the entry when a patched version ships (ADR 0037).
export const ALLOWLIST = [
  {
    id: 'GHSA-fc5q-39xc-vjpf',
    expires: '2026-12-17',
    reason:
      'picomatch ReDoS; no patched version exists (latest 4.0.7 is in <=4.0.7). Dev tooling only ' +
      '(vite, vitest, tinyglobby, fdir): no shipped code passes it an extglob pattern. ADR 0037.',
  },
]

const SEVERITIES = new Set(['info', 'low', 'moderate', 'high', 'critical'])
const GATED = new Set(['high', 'critical'])
const DATE = /^\d{4}-\d{2}-\d{2}$/

const advisoryId = (url) => (/\/advisories\/(GHSA-[a-z0-9-]+)$/i.exec(String(url)) || [])[1]

// Every advisory a package's `via` chains end at: [{id, severity}]. Throws on an unknown shape.
function leaves(name, vulns, seen = new Set()) {
  const pkg = vulns[name]
  if (!pkg || !Array.isArray(pkg.via)) throw new Error(`unknown package "${name}" in a via chain`)
  if (seen.has(name)) return []
  seen.add(name)
  return pkg.via.flatMap((v) => {
    if (typeof v === 'string') return leaves(v, vulns, seen)
    const id = v && advisoryId(v.url)
    if (!id || !SEVERITIES.has(v.severity)) throw new Error(`unknown advisory shape under "${name}"`)
    return [{ id, severity: v.severity }]
  })
}

// today: 'YYYY-MM-DD' (UTC). Returns {ok, lines}; never throws.
export function decide(audit, allowlist, today) {
  const lines = []
  try {
    for (const e of allowlist) {
      if (!/^GHSA-/.test(e.id) || !DATE.test(e.expires) || !e.reason) {
        throw new Error(`allowlist entry "${e.id}" needs an id, an expiry (YYYY-MM-DD) and a reason`)
      }
    }
    if (!audit || audit.auditReportVersion !== 2 || typeof audit.vulnerabilities !== 'object' || !audit.vulnerabilities) {
      throw new Error('not an npm audit report (auditReportVersion 2)')
    }
    const vulns = audit.vulnerabilities
    const used = new Set()
    const failed = new Set()
    for (const [name, pkg] of Object.entries(vulns)) {
      if (!SEVERITIES.has(pkg.severity)) throw new Error(`unknown severity under "${name}"`)
      if (!GATED.has(pkg.severity)) continue
      const gated = leaves(name, vulns).filter((l) => GATED.has(l.severity))
      if (gated.length === 0) throw new Error(`"${name}" is ${pkg.severity} but no high advisory explains it`)
      for (const l of gated) {
        const entry = allowlist.find((e) => e.id === l.id)
        if (l.severity === 'high' && entry && today <= entry.expires) used.add(l.id)
        else if (l.severity === 'high' && entry) failed.add(`${name}: ${l.id} (allowlist expired ${entry.expires})`)
        else failed.add(`${name}: ${l.id} (${l.severity})`)
      }
    }
    for (const f of failed) lines.push(`FAIL ${f}`)
    for (const id of used) lines.push(`tolerated ${id}: ${allowlist.find((e) => e.id === id).reason}`)
    if (failed.size > 0) return { ok: false, lines }
    return { ok: true, lines }
  } catch (err) {
    return { ok: false, lines: [`FAIL cannot decide: ${err.message}`] }
  }
}
