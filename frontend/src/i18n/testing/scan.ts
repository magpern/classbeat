import ts from 'typescript'

// The hard-coded string scan (ADR 0023 section 8, ML3). It reads a .tsx file with the TypeScript
// parser and reports the user-visible text written in it: JSX text, string literals in JSX
// expression children, and the string value of attributes that show text. Test-only code: the app
// never imports it.

/** Attributes whose value a person reads (ours and the DOM's). */
export const TEXT_ATTRIBUTES: ReadonlySet<string> = new Set([
  'aria-label',
  'aria-description',
  'aria-placeholder',
  'aria-roledescription',
  'aria-valuetext',
  'placeholder',
  'title',
  'alt',
  'label',
  'hint',
  'description',
  'caption',
  'heading',
  'subtitle',
  'helper',
  'legend',
  'summary',
  'eyebrow',
  'tooltip',
  'emptyText',
  'error',
  'message',
  'text',
])

/** Props whose name ends in one of these words also show text (`confirmLabel`, `emptyTitle`). */
const TEXT_ATTRIBUTE_SUFFIX = /(?:Label|Title|Text|Hint|Description|Placeholder|Caption|Heading)$/

export function isTextAttribute(name: string): boolean {
  return TEXT_ATTRIBUTES.has(name) || TEXT_ATTRIBUTE_SUFFIX.test(name)
}

/** Names that stay as written in every language; text made of these alone is not a finding. */
export const BRAND_NAMES: readonly string[] = [
  'Intelligent Cycling',
  'Swedebeat',
  'Classbeat',
  'classbeat',
  'Spotify',
  'MOWL',
  'Anthropic',
  'Claude',
  'Google',
  'GitHub',
  'Microsoft',
  'Paddle',
]

/** Paths (under src/) that are never scanned: the catalogues and the owner-only admin area. */
export function isExempt(path: string): boolean {
  return path.startsWith('i18n/') || path.startsWith('pages/admin/') || path === 'pages/AdminPage.tsx'
}

export type Finding = { line: number; text: string }

const ENTITY = /&(?:#\d+|#x[0-9a-f]+|[a-z][a-z0-9]*);/gi
// Word boundaries: a brand inside a longer word ("Googled") is not the brand, so its letters count.
const BRANDS = new RegExp(`\\b(?:${BRAND_NAMES.map((name) => name.replace(/\s+/g, '\\s+')).join('|')})\\b`, 'g')

/** True when `text` still holds a letter once entities and brand names are taken out. */
export function hasWords(text: string): boolean {
  return /\p{L}/u.test(text.replace(ENTITY, ' ').replace(BRANDS, ' '))
}

/** The string pieces an expression can show: literals, template heads, both arms of `?:`, `&&`, `||`, `??`, `+`. */
function pieces(node: ts.Node, out: ts.Node[]): void {
  if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) out.push(node)
  else if (ts.isTemplateExpression(node)) {
    out.push(node.head)
    for (const span of node.templateSpans) out.push(span.literal)
  } else if (ts.isParenthesizedExpression(node) || ts.isAsExpression(node) || ts.isNonNullExpression(node)) {
    pieces(node.expression, out)
  } else if (ts.isConditionalExpression(node)) {
    pieces(node.whenTrue, out)
    pieces(node.whenFalse, out)
  } else if (ts.isBinaryExpression(node)) {
    pieces(node.left, out)
    pieces(node.right, out)
  }
}

export function scanSource(source: string, fileName = 'file.tsx'): Finding[] {
  const file = ts.createSourceFile(fileName, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
  const found: Finding[] = []

  function report(node: ts.Node, text: string): void {
    const line = file.getLineAndCharacterOfPosition(node.getStart(file)).line + 1
    found.push({ line, text: text.replace(/\s+/g, ' ').trim().slice(0, 60) })
  }

  function check(nodes: ts.Node[]): void {
    for (const node of nodes) {
      const text = (node as ts.StringLiteral).text
      if (hasWords(text)) report(node, text)
    }
  }

  function visit(node: ts.Node): void {
    if (ts.isJsxText(node)) {
      if (hasWords(node.text)) report(node, node.text)
    } else if (ts.isJsxExpression(node) && node.expression !== undefined) {
      const parent = node.parent
      if (ts.isJsxElement(parent) || ts.isJsxFragment(parent)) {
        const list: ts.Node[] = []
        pieces(node.expression, list)
        check(list)
      }
    } else if (ts.isJsxAttribute(node) && isTextAttribute(node.name.getText(file))) {
      const value = node.initializer
      const list: ts.Node[] = []
      if (value !== undefined) {
        if (ts.isJsxExpression(value)) {
          if (value.expression !== undefined) pieces(value.expression, list)
        } else pieces(value, list)
      }
      check(list)
    }
    ts.forEachChild(node, visit)
  }
  visit(file)
  return found
}

// Bare number formatting for a value a person reads belongs in `src/i18n/format.ts` (ADR 0023 section 4).

/** Lines that call `toLocaleString`, `toLocaleDateString`, `toLocaleTimeString` or `toFixed`. */
export function scanBareFormatting(source: string): number[] {
  const file = ts.createSourceFile('file.ts', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
  const lines: number[] = []
  function visit(node: ts.Node): void {
    if (
      ts.isCallExpression(node) &&
      ts.isPropertyAccessExpression(node.expression) &&
      /^(toLocaleString|toLocaleDateString|toLocaleTimeString|toFixed)$/.test(node.expression.name.text)
    ) {
      lines.push(file.getLineAndCharacterOfPosition(node.getStart(file)).line + 1)
    }
    ts.forEachChild(node, visit)
  }
  visit(file)
  return lines
}
