import { dirname, join, normalize } from 'node:path'

// Test support: the modules a source file loads at run time, found in its text. Type-only imports
// (`import type`, `export type ... from`) leave no run-time import and are skipped; an inline
// `import { type X }` stays, because `verbatimModuleSyntax` keeps it as a side-effect import.

const COMMENTS = /\/\*[\s\S]*?\*\/|(^|[^:'"`])\/\/.*$/gm
const STATIC = /\b(?:import|export)\s+(type\s+)?(?:[^'";]*?\sfrom\s+)?['"]([^'"]+)['"]/g
const DYNAMIC = /\bimport\(\s*['"]([^'"]+)['"]\s*\)/g

/** Every module specifier the source loads at run time. */
export function runtimeImports(source: string): string[] {
  const text = source.replace(COMMENTS, '$1')
  const found: string[] = []
  for (const match of text.matchAll(STATIC)) {
    if (match[1] === undefined && match[2] !== undefined) found.push(match[2])
  }
  for (const match of text.matchAll(DYNAMIC)) {
    if (match[1] !== undefined) found.push(match[1])
  }
  return found
}

/** A relative specifier as a path from `src`, without an extension; other specifiers (packages) as null. */
export function srcPathOf(file: string, specifier: string): string | null {
  if (!specifier.startsWith('.')) return null
  return normalize(join(dirname(file), specifier)).replaceAll('\\', '/')
}
