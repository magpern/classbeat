import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync } from 'node:fs'
import { join, relative, sep } from 'node:path'

// Vitest hands back an empty string for ?raw CSS imports, so the sources are read from disk.
const srcDir = join(import.meta.dirname, '..')
const tokensCss = readFileSync(join(srcDir, 'styles', 'tokens.css'), 'utf8')
const tailwindCss = readFileSync(join(srcDir, 'styles', 'tailwind.css'), 'utf8')

function walk(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name)
    return entry.isDirectory() ? walk(path) : [path]
  })
}

// ADR 0013 section 2: every colour lives in tokens.css, nothing else defines one.
const sources: Record<string, string> = {}
for (const path of walk(srcDir)) {
  const name = relative(srcDir, path).split(sep).join('/')
  if (!/\.(css|ts|tsx)$/.test(name) || /\.test\.(ts|tsx)$/.test(name) || name === 'styles/tokens.css') continue
  sources[name] = readFileSync(path, 'utf8')
}

const COLOUR = /#(?:[0-9a-f]{8}|[0-9a-f]{6}|[0-9a-f]{3,4})\b|\b(?:rgba?|hsla?|hwb|oklch|oklab|lab|lch)\(/i

function withoutComments(source: string): string {
  return source.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|\s)\/\/.*$/gm, '$1')
}

describe('colour literals', () => {
  it('finds the sources it is meant to scan', () => {
    expect(Object.keys(sources).length).toBeGreaterThan(30)
    expect(Object.keys(sources)).toContain('styles/forms.css')
    expect(sources['styles/forms.css']?.length).toBeGreaterThan(100)
  })

  it('the checker recognises hex, rgb( and hsl(', () => {
    for (const literal of ['#fff', '#A1B2C3', '#a1b2c3d4', 'rgb(0 0 0)', 'rgba(0,0,0,.5)', 'hsl(0 0% 0%)']) {
      expect(COLOUR.test(literal), literal).toBe(true)
    }
    expect(COLOUR.test('var(--cb-text)')).toBe(false)
  })

  it.each(Object.entries(sources))('%s has none', (_path, source) => {
    expect(withoutComments(source)).not.toMatch(COLOUR)
  })

  it('tailwind.css only points at tokens', () => {
    expect(withoutComments(tailwindCss)).not.toMatch(COLOUR)
  })
})

// The other ways to write a colour (UI10, the MU done-when "no colour outside tokens.css"): a CSS
// colour keyword in a colour property or a style object, and a Tailwind palette utility. The
// palette is reset in tailwind.css (`--color-*: initial`), so such a utility would not even paint;
// the guard keeps it from being written. `transparent`, `currentColor` and `inherit` are not colours.
const KEYWORDS =
  'aliceblue antiquewhite aqua aquamarine azure beige bisque black blanchedalmond blue blueviolet brown burlywood cadetblue chartreuse chocolate coral cornflowerblue cornsilk crimson cyan darkblue darkcyan darkgoldenrod darkgray darkgreen darkgrey darkkhaki darkmagenta darkolivegreen darkorange darkorchid darkred darksalmon darkseagreen darkslateblue darkslategray darkslategrey darkturquoise darkviolet deeppink deepskyblue dimgray dimgrey dodgerblue firebrick floralwhite forestgreen fuchsia gainsboro ghostwhite gold goldenrod gray green greenyellow grey honeydew hotpink indianred indigo ivory khaki lavender lavenderblush lawngreen lemonchiffon lightblue lightcoral lightcyan lightgoldenrodyellow lightgray lightgreen lightgrey lightpink lightsalmon lightseagreen lightskyblue lightslategray lightslategrey lightsteelblue lightyellow lime limegreen linen magenta maroon mediumaquamarine mediumblue mediumorchid mediumpurple mediumseagreen mediumslateblue mediumspringgreen mediumturquoise mediumvioletred midnightblue mintcream mistyrose moccasin navajowhite navy oldlace olive olivedrab orange orangered orchid palegoldenrod palegreen paleturquoise palevioletred papayawhip peachpuff peru pink plum powderblue purple rebeccapurple red rosybrown royalblue saddlebrown salmon sandybrown seagreen seashell sienna silver skyblue slateblue slategray slategrey snow springgreen steelblue tan teal thistle tomato turquoise violet wheat white whitesmoke yellow yellowgreen'.split(
    ' ',
  )
const KEYWORD = String.raw`\b(?:${KEYWORDS.join('|')})\b`
// CSS: a keyword in the value of a property that takes a colour.
const CSS_PROPERTY = String.raw`(?:color|background(?:-color|-image)?|border(?:-(?:top|right|bottom|left|block|inline)(?:-(?:start|end))?)?(?:-color)?|outline(?:-color)?|fill|stroke|stop-color|flood-color|caret-color|accent-color|text-decoration(?:-color)?|box-shadow|text-shadow|column-rule(?:-color)?)`
const CSS_KEYWORD = new RegExp(String.raw`(?:^|[;{\s])${CSS_PROPERTY}\s*:[^;{}]*${KEYWORD}`, 'i')
// ts/tsx: a keyword as the value of a colour key in a style object.
const STYLE_KEYWORD = new RegExp(
  String.raw`\b(?:color|background|backgroundColor|borderColor|outlineColor|fill|stroke|stopColor|caretColor|accentColor)\s*:\s*['"` + '`' + String.raw`][^'"` + '`' + String.raw`]*${KEYWORD}`,
  'i',
)
const PALETTE =
  /\b(?:bg|text|border|ring|fill|stroke|from|via|to|outline|decoration|divide|accent|caret|shadow|placeholder)-(?:white|black|(?:slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose)-\d{2,3})\b/

function otherColour(name: string, source: string): string | null {
  const code = withoutComments(source)
  if (name.endsWith('.css')) return CSS_KEYWORD.exec(code)?.[0] ?? null
  return STYLE_KEYWORD.exec(code)?.[0] ?? PALETTE.exec(code)?.[0] ?? null
}

describe('colour keywords and palette utilities', () => {
  it('the checker recognises them (a planted colour is found)', () => {
    expect(otherColour('x.css', '.a { color: white; }')).not.toBeNull()
    expect(otherColour('x.css', '.a { border-top: 1px solid red; }')).not.toBeNull()
    expect(otherColour('x.css', '.a { background: linear-gradient(black, var(--cb-bg)); }')).not.toBeNull()
    expect(otherColour('x.tsx', "<div style={{ backgroundColor: 'tomato' }} />")).not.toBeNull()
    expect(otherColour('x.tsx', '<div className="bg-red-500 p-2" />')).not.toBeNull()
    expect(otherColour('x.tsx', '<div className="text-white" />')).not.toBeNull()
    expect(otherColour('x.tsx', '<div className="hover:border-blue-600" />')).not.toBeNull()
  })

  it('does not take tokens, transparent, currentColor or ordinary words for colours', () => {
    expect(otherColour('x.css', '.a { color: var(--cb-text); border: 1px solid transparent; fill: currentColor; }')).toBeNull()
    expect(otherColour('x.css', '.a { white-space: pre-wrap; content: "red"; }')).toBeNull()
    expect(otherColour('x.tsx', '<div className="bg-surface text-muted border-line white-space" />')).toBeNull()
    expect(otherColour('x.tsx', "const color = 'brand'")).toBeNull()
  })

  it.each(Object.entries(sources))('%s has none', (name, source) => {
    expect(otherColour(name, source)).toBeNull()
  })

  it('the page shell files outside src have none either', () => {
    for (const file of [join(srcDir, '..', 'index.html'), join(srcDir, '..', 'public', 'theme-init.js')]) {
      const code = withoutComments(readFileSync(file, 'utf8'))
      expect(code, file).not.toMatch(COLOUR)
      expect(code, file).not.toMatch(/style\s*=/i)
    }
  })
})

// WCAG contrast from the token values (ADR 0013 sections 2 and 10).
function block(css: string, selector: string): string {
  const start = css.indexOf(selector + ' {')
  expect(start, selector).toBeGreaterThanOrEqual(0)
  return css.slice(start, css.indexOf('}', start))
}

function tokens(css: string): Record<string, string> {
  const out: Record<string, string> = {}
  for (const m of css.matchAll(/--cb-([a-z0-9-]+):\s*(#[0-9a-f]{6})/gi)) out[m[1] as string] = m[2] as string
  return out
}

function luminance(hex: string): number {
  const channel = (i: number) => {
    const c = parseInt(hex.slice(1 + i * 2, 3 + i * 2), 16) / 255
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
  }
  return 0.2126 * channel(0) + 0.7152 * channel(1) + 0.0722 * channel(2)
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x) as [number, number]
  return (hi + 0.05) / (lo + 0.05)
}

const dark = tokens(block(tokensCss, ':root'))
const light = { ...dark, ...tokens(block(tokensCss, ":root[data-theme='light']")) }
const system = { ...dark, ...tokens(block(tokensCss, "  :root[data-theme='system']")) }

describe('contrast', () => {
  it.each([
    ['dark', dark],
    ['light', light],
  ])('%s: text, muted and brand pairs meet WCAG AA', (_name, t) => {
    expect(contrast(t.text as string, t.bg as string)).toBeGreaterThanOrEqual(4.5)
    for (const bg of ['bg', 'surface', 'raised']) {
      expect(contrast(t.muted as string, t[bg] as string), `muted on ${bg}`).toBeGreaterThanOrEqual(4.5)
      expect(contrast(t.text as string, t[bg] as string), `text on ${bg}`).toBeGreaterThanOrEqual(4.5)
    }
    expect(contrast(t['on-brand'] as string, t.brand as string)).toBeGreaterThanOrEqual(4.5)
    for (const status of ['ok', 'warn', 'bad']) {
      expect(contrast(t[status] as string, t[`${status}-soft`] as string), status).toBeGreaterThanOrEqual(4.5)
    }
  })

  it('the system theme (light device) has the same values as light', () => {
    for (const key of ['bg', 'surface', 'raised', 'text', 'muted', 'brand', 'on-brand']) {
      expect(system[key], key).toBe(light[key])
    }
  })

  it('zone colours and stage text meet 3:1 on the stage', () => {
    for (let zone = 1; zone <= 7; zone++) {
      expect(contrast(dark[`zone-${zone}`] as string, dark.stage as string), `zone ${zone}`).toBeGreaterThanOrEqual(3)
    }
    expect(contrast(dark['stage-text'] as string, dark.stage as string)).toBeGreaterThanOrEqual(4.5)
    expect(contrast(dark['stage-muted'] as string, dark.stage as string)).toBeGreaterThanOrEqual(3)
  })

  it('the zones are the ADR 0013 values: one red for zones 5 to 7', () => {
    expect([dark['zone-5'], dark['zone-6'], dark['zone-7']]).toEqual(['#f0483c', '#f0483c', '#f0483c'])
    expect(dark['zone-1']).toBe('#8e99a8')
  })
})

describe('reduced motion', () => {
  it('turns animations and transitions off', () => {
    const start = tailwindCss.indexOf('@media (prefers-reduced-motion: reduce)')
    expect(start).toBeGreaterThanOrEqual(0)
    const rule = tailwindCss.slice(start)
    expect(rule).toMatch(/animation-duration:\s*0\.01ms\s*!important/)
    expect(rule).toMatch(/transition-duration:\s*0\.01ms\s*!important/)
  })

  it('the old teal and amber tokens are gone', () => {
    expect(tokensCss).not.toMatch(/--cb-color-/)
  })
})
